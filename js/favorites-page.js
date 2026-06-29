/**
 * Favorites Page
 * 收藏夹页面逻辑：
 *  - 合并本地收藏与 data 分支 favorites.jsonl（跨设备恢复）
 *  - 按研究方向 TAG 分组/筛选
 *  - 展示每篇的深度分析状态，触发/重跑深度分析（repository_dispatch）
 *  - 轮询 data/deep/{id}.json 出现后展示
 *  - 处理 tags_pending.json：用户确认后写回 tags.json
 */

const DISPATCH_EVENT = 'deep-analyze';
const POLL_INTERVAL_MS = 15000;   // 轮询间隔
const POLL_TIMEOUT_MS = 5 * 60 * 1000; // 轮询超时

let activeTagFilter = null;       // 当前 TAG 筛选；null 表示全部
let deepCache = {};               // id -> deep json
let knownTags = [];               // tags.json 中的标签

document.addEventListener('DOMContentLoaded', init);

async function init() {
    bindModal();
    await restoreFromRemote();
    if (GitHubClient.hasToken()) {
        const res = await Favorites.syncAllToRemote();
        if (!res.ok) console.warn('本地收藏回填远端失败:', res.message || res);
    }
    await loadKnownTags();
    await loadDeepForFavorites();
    render();
}

/** 从 data 分支 favorites.jsonl 恢复收藏到本地（跨设备） */
async function restoreFromRemote() {
    const res = await Favorites.restoreFromRemote();
    if (!res.ok) console.warn('收藏远端恢复失败:', res.message || res);
}

/** 读取已确认标签库 tags.json */
async function loadKnownTags() {
    const res = await GitHubClient.getFile('data/tags.json');
    if (res.exists && res.content) {
        try {
            const data = JSON.parse(res.content);
            knownTags = Array.isArray(data.tags) ? data.tags : [];
        } catch (e) { knownTags = []; }
    }
}

/** 为所有收藏论文读取已有的深度分析（如已存在） */
async function loadDeepForFavorites() {
    const ids = Favorites.getIds();
    await Promise.all(ids.map(async id => {
        const res = await GitHubClient.getFile(`data/deep/${id}.json`);
        if (res.exists && res.content) {
            try { deepCache[id] = JSON.parse(res.content); } catch (e) { /* ignore */ }
        }
    }));
}

/** 读取待确认标签 */
async function loadPendingTags() {
    const res = await GitHubClient.getFile('data/tags_pending.json');
    let pending = [];
    if (res.exists && res.content) {
        try { pending = (JSON.parse(res.content).pending) || []; } catch (e) { pending = []; }
    }
    renderPendingTags(pending, res.sha || null);
}

/* ---------------- 渲染 ---------------- */

function render() {
    renderStats();
    renderTagFilter();
    renderList();
}

function renderStats() {
    const ids = Favorites.getIds();
    const analyzed = ids.filter(id => deepCache[id]).length;
    const el = document.getElementById('favStats');
    el.textContent = `共 ${ids.length} 篇收藏 · 已深度分析 ${analyzed} 篇 · 待分析 ${ids.length - analyzed} 篇`;
}

/** 收集所有收藏论文出现过的 tags（来自手动标签 + deep 结果） */
function collectTags() {
    const counter = {};
    const meta = Favorites.getMeta();
    Favorites.getIds().forEach(id => {
        getPaperTags(id, meta).forEach(t => {
            counter[t] = (counter[t] || 0) + 1;
        });
    });
    return counter;
}

function renderTagFilter() {
    const bar = document.getElementById('tagFilterBar');
    const counter = collectTags();
    const tags = Object.keys(counter).sort();
    bar.innerHTML = '';

    const allBtn = document.createElement('button');
    allBtn.className = 'tag-filter-chip' + (activeTagFilter === null ? ' active' : '');
    allBtn.textContent = `全部 (${Favorites.getIds().length})`;
    allBtn.onclick = () => { activeTagFilter = null; render(); };
    bar.appendChild(allBtn);

    tags.forEach(t => {
        const chip = document.createElement('button');
        chip.className = 'tag-filter-chip' + (activeTagFilter === t ? ' active' : '');
        chip.textContent = `${t} (${counter[t]})`;
        chip.onclick = () => { activeTagFilter = (activeTagFilter === t ? null : t); render(); };
        bar.appendChild(chip);
    });
}

function escapeHtml(s) {
    return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function normalizeTagName(tag) {
    return (tag || '').trim().replace(/\s+/g, ' ');
}

function uniqueTags(tags) {
    const seen = new Set();
    const out = [];
    (tags || []).forEach(t => {
        const name = normalizeTagName(t);
        const key = name.toLowerCase();
        if (name && !seen.has(key)) {
            seen.add(key);
            out.push(name);
        }
    });
    return out;
}

function getPaperTags(id, metaMap = Favorites.getMeta()) {
    const metaTags = (metaMap[id] && Array.isArray(metaMap[id].tags)) ? metaMap[id].tags : [];
    // 标签以用户手动维护为准，不再依赖 LLM 自动输出的 tags。
    return uniqueTags(metaTags);
}

function getKnownTagNames() {
    return uniqueTags(knownTags.map(t => t.name).filter(Boolean));
}

function formatCategories(categories) {
    if (Array.isArray(categories)) return categories.filter(Boolean).join(', ');
    return categories || '';
}

function formatDateLabel(date) {
    if (!date) return '';
    const d = new Date(date);
    if (Number.isNaN(d.getTime())) return date;
    return d.toLocaleDateString('en-US', { year: 'numeric', month: 'numeric', day: 'numeric' });
}

function getAffiliationTypeLabel(type) {
    const map = {
        industry: '工业界',
        academia: '学术界',
        collaboration: '产学合作',
        unknown: '未知'
    };
    return map[type] || type || '未知';
}

function renderAttributeBadges(m) {
    const badges = [];
    if (m.is_industrial_paper) badges.push('<span class="favorite-attr-badge badge-industry">🏭 工业界</span>');
    if (m.is_ab_test) badges.push('<span class="favorite-attr-badge badge-abtest">🧪 AB实验</span>');
    if (!m.is_industrial_paper && !m.is_ab_test && m.affiliation_type) {
        badges.push(`<span class="favorite-attr-badge badge-affiliation">${escapeHtml(getAffiliationTypeLabel(m.affiliation_type))}</span>`);
    }
    return badges.join('');
}

function renderFavoriteInfo(m) {
    const rows = [];
    if (m.authors) rows.push(`<div class="favorite-info-row"><span>作者</span><strong>${escapeHtml(m.authors)}</strong></div>`);
    if (m.org_display) rows.push(`<div class="favorite-info-row"><span>机构</span><strong>${escapeHtml(m.org_display)}</strong></div>`);
    const categories = formatCategories(m.categories);
    if (categories) rows.push(`<div class="favorite-info-row"><span>类别</span><strong>${escapeHtml(categories)}</strong></div>`);
    const affiliation = getAffiliationTypeLabel(m.affiliation_type || 'unknown');
    rows.push(`<div class="favorite-info-row compact"><span>属性</span><strong>${renderAttributeBadges(m) || escapeHtml(affiliation)}</strong></div>`);
    return rows.length ? `<div class="favorite-info-grid">${rows.join('')}</div>` : '';
}

function buildChatGPTPaperPrompt(id, meta) {
    const title = meta.title || id;
    const pdf = meta.pdf || `https://arxiv.org/pdf/${id}`;
    return `请你阅读并分析这篇论文：${title}\n${pdf}\n请用中文给出结构化解读。`;
}

function buildChatGPTPaperUrl(id, meta) {
    return `https://chatgpt.com/?q=${encodeURIComponent(buildChatGPTPaperPrompt(id, meta || {}))}`;
}

function getTagSuggestions(query) {
    const q = normalizeTagName(query).toLowerCase();
    if (!q) return [];
    return getKnownTagNames()
        .filter(name => name.toLowerCase().includes(q))
        .slice(0, 8);
}

function renderTagBadges(tags, paperId, removable = true) {
    if (!tags || tags.length === 0) {
        return '<span class="fav-tag empty">未打标签</span>';
    }
    return tags.map(t => `
        <span class="fav-tag">
            ${escapeHtml(t)}
            ${removable ? `<button class="remove-tag-btn" data-id="${escapeHtml(paperId)}" data-tag="${escapeHtml(t)}" title="移除标签">×</button>` : ''}
        </span>
    `).join('');
}

function getSuggestionDropdown(input) {
    return input.closest('.manual-tag-input-wrap')?.querySelector('.tag-suggestions-dropdown');
}

function renderCustomTagSuggestions(input) {
    const dropdown = getSuggestionDropdown(input);
    if (!dropdown) return;

    const query = normalizeTagName(input.value);
    const suggestions = getTagSuggestions(query);
    dropdown.innerHTML = '';

    if (!query || suggestions.length === 0) {
        dropdown.classList.remove('active');
        return;
    }

    suggestions.forEach((tag, index) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'tag-suggestion-item' + (index === 0 ? ' active' : '');
        item.setAttribute('role', 'option');
        item.dataset.tag = tag;
        item.innerHTML = highlightSuggestion(tag, query);
        item.addEventListener('mousedown', (e) => {
            e.preventDefault();
            input.value = tag;
            hideTagSuggestions(input);
            input.focus();
        });
        dropdown.appendChild(item);
    });
    dropdown.classList.add('active');
}

function highlightSuggestion(tag, query) {
    const lowerTag = tag.toLowerCase();
    const lowerQuery = query.toLowerCase();
    const idx = lowerTag.indexOf(lowerQuery);
    if (idx === -1) return escapeHtml(tag);
    return `${escapeHtml(tag.slice(0, idx))}<mark>${escapeHtml(tag.slice(idx, idx + query.length))}</mark>${escapeHtml(tag.slice(idx + query.length))}`;
}

function hideTagSuggestions(input) {
    const dropdown = getSuggestionDropdown(input);
    if (dropdown) dropdown.classList.remove('active');
}

function moveActiveSuggestion(input, delta) {
    const dropdown = getSuggestionDropdown(input);
    if (!dropdown || !dropdown.classList.contains('active')) {
        renderCustomTagSuggestions(input);
        return;
    }
    const items = Array.from(dropdown.querySelectorAll('.tag-suggestion-item'));
    if (items.length === 0) return;
    let index = items.findIndex(item => item.classList.contains('active'));
    index = (index + delta + items.length) % items.length;
    items.forEach(item => item.classList.remove('active'));
    items[index].classList.add('active');
    items[index].scrollIntoView({ block: 'nearest' });
}

function selectActiveSuggestion(input) {
    const dropdown = getSuggestionDropdown(input);
    if (!dropdown || !dropdown.classList.contains('active')) return false;
    const active = dropdown.querySelector('.tag-suggestion-item.active');
    if (!active) return false;
    input.value = active.dataset.tag || active.textContent.trim();
    hideTagSuggestions(input);
    return true;
}

function renderList() {
    const container = document.getElementById('favoritesList');
    let ids = Favorites.getIds();

    const meta = Favorites.getMeta();
    if (activeTagFilter) {
        ids = ids.filter(id => getPaperTags(id, meta).includes(activeTagFilter));
    }

    if (ids.length === 0) {
        container.innerHTML = '<div class="loading-container"><p>还没有收藏，去主页点星收藏论文吧。</p></div>';
        return;
    }

    container.innerHTML = '';
    ids.forEach(id => {
        const m = meta[id] || { title: id, date: '', pdf: `https://arxiv.org/pdf/${id}`, abs: `https://arxiv.org/abs/${id}` };
        const deep = deepCache[id];
        const tags = getPaperTags(id, meta);
        const tagsHtml = renderTagBadges(tags, id);
        const summaryText = m.summary || '';
        const infoHtml = renderFavoriteInfo(m);
        const chatgptUrl = buildChatGPTPaperUrl(id, m);

        const statusHtml = deep
            ? '<span class="deep-status done">已深度分析</span>'
            : '<span class="deep-status none">未分析</span>';

        const card = document.createElement('div');
        card.className = 'fav-card';
        card.innerHTML = `
            <div class="fav-card-main">
                <h3 class="fav-card-title">${escapeHtml(m.title)}</h3>
                <div class="fav-card-meta">
                    <span class="fav-card-id">${escapeHtml(id)}</span>
                    ${m.date ? `<span class="fav-card-date">${escapeHtml(formatDateLabel(m.date))}</span>` : ''}
                    ${statusHtml}
                </div>
                ${infoHtml}
                <div class="fav-card-tags">${tagsHtml}</div>
                <details class="favorite-summary-block">
                    <summary class="favorite-summary-title">
                        <span>简要摘要</span>
                        <span class="favorite-summary-state">${summaryText ? '已填写，点击展开编辑' : '暂无，点击补充'}</span>
                    </summary>
                    <div class="favorite-summary-content">
                        <textarea class="favorite-summary-input" data-id="${escapeHtml(id)}" placeholder="暂无摘要，可自行补充...">${escapeHtml(summaryText)}</textarea>
                        <div class="favorite-summary-actions">
                            <button class="button save-summary-btn" data-id="${escapeHtml(id)}">保存摘要</button>
                            <span class="summary-save-status" data-id="${escapeHtml(id)}"></span>
                        </div>
                    </div>
                </details>
                <div class="manual-tag-editor" data-id="${escapeHtml(id)}">
                    <div class="manual-tag-input-wrap">
                        <input class="manual-tag-input" data-id="${escapeHtml(id)}" autocomplete="off" placeholder="添加研究方向 TAG，如 Semantic Identifier...">
                        <button class="button add-paper-tag-btn" data-id="${escapeHtml(id)}">添加标签</button>
                        <div class="tag-suggestions-dropdown" role="listbox"></div>
                    </div>
                    <div class="tag-suggestion-hint">输入时会从已有标签库中匹配候选；也可以直接输入新标签并自动入库。</div>
                </div>
            </div>
            <div class="fav-card-actions">
                <a class="button icon-button" href="${escapeHtml(m.abs || ('https://arxiv.org/abs/' + id))}" target="_blank" title="arXiv">abs</a>
                <a class="button favorite-ai-btn" href="${escapeHtml(chatgptUrl)}" target="_blank" title="用 ChatGPT 分析论文" aria-label="用 ChatGPT 分析论文">
                    <span class="favorite-ai-spark">✦</span><span>AI</span>
                </a>
                ${deep ? `<button class="button view-deep-btn" data-id="${escapeHtml(id)}">查看分析</button>` : ''}
                <button class="button primary analyze-btn" data-id="${escapeHtml(id)}">${deep ? '重跑分析' : '深度分析'}</button>
                <button class="button unfav-btn" data-id="${escapeHtml(id)}" title="取消收藏">✕</button>
            </div>
        `;
        container.appendChild(card);
    });

    container.querySelectorAll('.view-deep-btn').forEach(btn => {
        btn.onclick = () => showDeepModal(btn.dataset.id);
    });
    container.querySelectorAll('.analyze-btn').forEach(btn => {
        btn.onclick = () => triggerDeepAnalysis(btn.dataset.id, btn);
    });
    container.querySelectorAll('.unfav-btn').forEach(btn => {
        btn.onclick = () => {
            Favorites.remove(btn.dataset.id);
            delete deepCache[btn.dataset.id];
            render();
        };
    });
    container.querySelectorAll('.add-paper-tag-btn').forEach(btn => {
        btn.onclick = () => {
            const id = btn.dataset.id;
            const input = btn.closest('.manual-tag-editor')?.querySelector('.manual-tag-input');
            addTagToPaper(id, input ? input.value : '', btn);
        };
    });
    container.querySelectorAll('.manual-tag-input').forEach(input => {
        input.addEventListener('input', () => renderCustomTagSuggestions(input));
        input.addEventListener('focus', () => renderCustomTagSuggestions(input));
        input.addEventListener('blur', () => {
            // 延迟隐藏，确保鼠标点击候选项时 mousedown 能先触发
            setTimeout(() => hideTagSuggestions(input), 120);
        });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                moveActiveSuggestion(input, 1);
                return;
            }
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                moveActiveSuggestion(input, -1);
                return;
            }
            if (e.key === 'Escape') {
                hideTagSuggestions(input);
                return;
            }
            if (e.key === 'Enter') {
                e.preventDefault();
                if (selectActiveSuggestion(input)) return;
                const btn = input.closest('.manual-tag-editor')?.querySelector('.add-paper-tag-btn');
                addTagToPaper(input.dataset.id, input.value, btn);
            }
        });
    });
    container.querySelectorAll('.remove-tag-btn').forEach(btn => {
        btn.onclick = () => removeTagFromPaper(btn.dataset.id, btn.dataset.tag, btn);
    });
    container.querySelectorAll('.save-summary-btn').forEach(btn => {
        btn.onclick = () => {
            const input = btn.closest('.favorite-summary-block')?.querySelector('.favorite-summary-input');
            savePaperSummary(btn.dataset.id, input ? input.value : '', btn);
        };
    });
}

/* ---------------- 手动标签管理 ---------------- */

function setLocalPaperTags(id, tags) {
    const meta = Favorites.getMeta();
    meta[id] = meta[id] || { title: id, pdf: `https://arxiv.org/pdf/${id}`, abs: `https://arxiv.org/abs/${id}` };
    meta[id].tags = uniqueTags(tags);
    meta[id].tagsEditedLocally = true;
    meta[id].tagsEditedAt = new Date().toISOString();
    Favorites._saveMeta(meta);
}

function getLocalPaperTags(id) {
    const meta = Favorites.getMeta();
    return (meta[id] && Array.isArray(meta[id].tags)) ? meta[id].tags : [];
}

function setLocalPaperSummary(id, summary) {
    const meta = Favorites.getMeta();
    meta[id] = meta[id] || { title: id, pdf: `https://arxiv.org/pdf/${id}`, abs: `https://arxiv.org/abs/${id}` };
    meta[id].summary = (summary || '').trim();
    meta[id].summaryEditedLocally = true;
    meta[id].summaryEditedAt = new Date().toISOString();
    Favorites._saveMeta(meta);
}

async function ensureTagInLibrary(tagName) {
    const name = normalizeTagName(tagName);
    if (!name) return { ok: false, message: '标签为空' };
    const existing = getKnownTagNames().some(t => t.toLowerCase() === name.toLowerCase());
    if (existing) return { ok: true };

    const res = await GitHubClient.updateFileWithRetry('data/tags.json', (old) => {
        let data = { tags: [] };
        if (old) { try { data = JSON.parse(old); } catch (e) { data = { tags: [] }; } }
        if (!Array.isArray(data.tags)) data.tags = [];
        const exists = data.tags.some(t => (t.name || '').toLowerCase() === name.toLowerCase());
        if (!exists) data.tags.push({ name, desc: '用户手动添加的研究方向标签', count: 1, created_by: 'user' });
        return JSON.stringify(data, null, 2);
    }, `tags: add "${name}"`);
    if (res.ok) {
        knownTags.push({ name, desc: '用户手动添加的研究方向标签', count: 1, created_by: 'user' });
    }
    return res;
}

async function syncFavoriteTagsToRemote(id, tags) {
    const meta = { ...(Favorites.metaOf(id) || {}), tags: uniqueTags(tags), has_deep: !!deepCache[id] };
    return GitHubClient.updateFileWithRetry('data/favorites.jsonl', (old) => {
        const rows = Favorites.parseRemoteRows(old);
        const existing = rows.find(r => r.id === id) || {};
        const filtered = rows.filter(r => r.id !== id);
        filtered.push(Favorites.metaToRemoteRow(id, meta, existing));
        return filtered.map(r => JSON.stringify(r)).join('\n') + '\n';
    }, `favorites: update tags for ${id}`);
}

async function syncFavoriteMetaToRemote(id) {
    const meta = { ...(Favorites.metaOf(id) || {}), has_deep: !!deepCache[id] };
    return GitHubClient.updateFileWithRetry('data/favorites.jsonl', (old) => {
        const rows = Favorites.parseRemoteRows(old);
        const existing = rows.find(r => r.id === id) || {};
        const filtered = rows.filter(r => r.id !== id);
        filtered.push(Favorites.metaToRemoteRow(id, meta, existing));
        return filtered.map(r => JSON.stringify(r)).join('\n') + '\n';
    }, `favorites: update metadata for ${id}`);
}

async function savePaperSummary(id, rawSummary, btn) {
    if (!GitHubClient.hasToken()) {
        alert('请先在「设置」页配置 GitHub PAT，才能同步摘要。');
        return;
    }
    const summary = (rawSummary || '').trim();
    setLocalPaperSummary(id, summary);

    const statusEl = Array.from(document.querySelectorAll('.summary-save-status')).find(el => el.dataset.id === id);
    if (btn) { btn.disabled = true; btn.textContent = '保存中...'; }
    if (statusEl) { statusEl.textContent = ''; statusEl.className = 'summary-save-status'; }

    const res = await syncFavoriteMetaToRemote(id);
    if (btn) { btn.disabled = false; btn.textContent = '保存摘要'; }
    if (statusEl) {
        statusEl.textContent = res.ok ? '已保存' : '保存失败';
        statusEl.classList.toggle('success', !!res.ok);
        statusEl.classList.toggle('error', !res.ok);
        setTimeout(() => { statusEl.textContent = ''; }, 2500);
    }
    if (!res.ok) alert(`摘要已保存到本地，但远端同步失败：${res.message || '未知错误'}`);
}

async function addTagToPaper(id, rawTag, btn) {
    const tag = normalizeTagName(rawTag);
    if (!tag) return;
    if (!GitHubClient.hasToken()) {
        alert('请先在「设置」页配置 GitHub PAT，才能同步标签。');
        return;
    }
    if (btn) { btn.disabled = true; btn.textContent = '添加中...'; }

    const current = getLocalPaperTags(id);
    const next = uniqueTags([...current, tag]);
    setLocalPaperTags(id, next);

    const libRes = await ensureTagInLibrary(tag);
    const favRes = await syncFavoriteTagsToRemote(id, next);

    if (btn) { btn.disabled = false; btn.textContent = '添加标签'; }
    if (!libRes.ok || !favRes.ok) {
        alert(`标签已保存到本地，但远端同步失败：${libRes.message || favRes.message || '未知错误'}`);
    }
    render();
}

async function removeTagFromPaper(id, tag, btn) {
    if (!GitHubClient.hasToken()) {
        alert('请先在「设置」页配置 GitHub PAT，才能同步标签。');
        return;
    }
    if (btn) btn.disabled = true;
    const next = getLocalPaperTags(id).filter(t => t.toLowerCase() !== tag.toLowerCase());
    setLocalPaperTags(id, next);
    const res = await syncFavoriteTagsToRemote(id, next);
    if (!res.ok) alert(`远端同步失败：${res.message || '未知错误'}`);
    render();
}

/* ---------------- 触发深度分析 ---------------- */

async function triggerDeepAnalysis(id, btn) {
    if (!GitHubClient.hasToken()) {
        alert('请先在「设置」页配置 GitHub PAT，才能触发深度分析。');
        window.location.href = 'settings.html';
        return;
    }
    const meta = Favorites.metaOf(id) || {};
    btn.disabled = true;
    btn.textContent = '触发中...';

    const knownNames = knownTags.map(t => t.name);
    const res = await GitHubClient.dispatch(DISPATCH_EVENT, {
        id: id,
        pdf: meta.pdf || `https://arxiv.org/pdf/${id}`,
        title: meta.title || id,
        date: meta.date || '',
        known_tags: knownNames,
        language: 'Chinese'
    });

    if (!res.ok) {
        btn.disabled = false;
        btn.textContent = '深度分析';
        alert(`触发失败 (${res.status}): ${res.message || '未知错误'}`);
        return;
    }

    btn.textContent = '分析中...';
    pollForResult(id, btn);
}

/** 轮询 data/deep/{id}.json 出现 */
function pollForResult(id, btn) {
    const start = Date.now();
    const timer = setInterval(async () => {
        if (Date.now() - start > POLL_TIMEOUT_MS) {
            clearInterval(timer);
            if (btn) { btn.disabled = false; btn.textContent = '重试分析'; }
            alert('深度分析超时，请稍后在收藏夹页刷新查看，或重试。');
            return;
        }
        const res = await GitHubClient.getFile(`data/deep/${id}.json`);
        if (res.exists && res.content) {
            try {
                deepCache[id] = JSON.parse(res.content);
                clearInterval(timer);
                render();
            } catch (e) { /* keep polling */ }
        }
    }, POLL_INTERVAL_MS);
}

/* ---------------- 深度分析弹窗 ---------------- */

function section(title, content) {
    if (!content) return '';
    const body = Array.isArray(content)
        ? '<ul>' + content.map(x => `<li>${escapeHtml(x)}</li>`).join('') + '</ul>'
        : `<p>${escapeHtml(content)}</p>`;
    return `<div class="paper-section"><h4>${title}</h4>${body}</div>`;
}

function showDeepModal(id) {
    const deep = deepCache[id];
    if (!deep) return;
    const d = deep.deep || {};
    document.getElementById('deepModalTitle').textContent = deep.title || id;

    const tagsHtml = renderTagBadges(getPaperTags(id), id, false);
    const body = `
        <div class="paper-details">
            <p class="deep-meta">模型: ${escapeHtml(deep.model || '')} · 分析时间: ${escapeHtml((deep.analyzed_at || '').slice(0, 10))}</p>
            <div class="fav-card-tags">${tagsHtml}</div>
            <div class="paper-sections">
                ${section('领域背景', d.background)}
                ${section('核心问题', d.problem)}
                ${section('研究动机', d.motivation)}
                ${section('方法总览', d.method_overview)}
                ${section('方法细节', d.method_details)}
                ${section('实验设置', d.experiments)}
                ${section('结果与分析', d.results_analysis)}
                ${section('结论', d.conclusion)}
                ${section('创新点', d.innovations)}
                ${section('局限性', d.limitations)}
                ${section('未来方向', d.future_work)}
                ${section('与相关工作对比', d.related_comparison)}
            </div>
        </div>
    `;
    document.getElementById('deepModalBody').innerHTML = body;
    const modal = document.getElementById('deepModal');
    modal.classList.add('active');
    document.body.style.overflow = 'hidden';
}

function bindModal() {
    const modal = document.getElementById('deepModal');
    const close = () => { modal.classList.remove('active'); document.body.style.overflow = ''; };
    document.getElementById('closeDeepModal').onclick = close;
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
}

/* ---------------- 待确认标签 ---------------- */

function renderPendingTags(pending, sha) {
    const box = document.getElementById('pendingTagsBox');
    const list = document.getElementById('pendingTagsList');
    if (!pending || pending.length === 0) {
        box.style.display = 'none';
        return;
    }
    box.style.display = '';
    list.innerHTML = '';
    pending.forEach((p, idx) => {
        const row = document.createElement('div');
        row.className = 'pending-tag-row';
        row.innerHTML = `
            <div class="pending-tag-info">
                <strong>${escapeHtml(p.name)}</strong>
                <span>${escapeHtml(p.desc || '')}</span>
            </div>
            <div class="pending-tag-actions">
                <button class="button primary confirm-tag-btn" data-idx="${idx}">确认入库</button>
                <button class="button reject-tag-btn" data-idx="${idx}">忽略</button>
            </div>
        `;
        list.appendChild(row);
    });

    list.querySelectorAll('.confirm-tag-btn').forEach(btn => {
        btn.onclick = () => resolvePendingTag(pending, parseInt(btn.dataset.idx, 10), true, btn);
    });
    list.querySelectorAll('.reject-tag-btn').forEach(btn => {
        btn.onclick = () => resolvePendingTag(pending, parseInt(btn.dataset.idx, 10), false, btn);
    });
}

/**
 * 确认/忽略一个待定标签。
 * 确认：合并进 tags.json；无论确认或忽略，都从 tags_pending.json 移除。
 */
async function resolvePendingTag(pending, idx, confirm, btn) {
    if (!GitHubClient.hasToken()) {
        alert('请先在「设置」页配置 GitHub PAT。');
        return;
    }
    const tag = pending[idx];
    if (!tag) return;
    btn.disabled = true;
    btn.textContent = '处理中...';

    // 1) 确认则写入 tags.json（乐观锁合并去重）
    if (confirm) {
        const r1 = await GitHubClient.updateFileWithRetry('data/tags.json', (old) => {
            let data = { tags: [] };
            if (old) { try { data = JSON.parse(old); } catch (e) { data = { tags: [] }; } }
            if (!Array.isArray(data.tags)) data.tags = [];
            const exists = data.tags.some(t => (t.name || '').toLowerCase() === tag.name.toLowerCase());
            if (!exists) data.tags.push({ name: tag.name, desc: tag.desc || '', count: 1 });
            return JSON.stringify(data, null, 2);
        }, `tags: confirm "${tag.name}"`);
        if (!r1.ok) {
            btn.disabled = false;
            btn.textContent = '确认入库';
            alert('写入 tags.json 失败: ' + (r1.message || ''));
            return;
        }
    }

    // 2) 从 tags_pending.json 移除该项（乐观锁）
    const r2 = await GitHubClient.updateFileWithRetry('data/tags_pending.json', (old) => {
        let data = { pending: [] };
        if (old) { try { data = JSON.parse(old); } catch (e) { data = { pending: [] }; } }
        data.pending = (data.pending || []).filter(p => (p.name || '').toLowerCase() !== tag.name.toLowerCase());
        return JSON.stringify(data, null, 2);
    }, `tags: resolve pending "${tag.name}"`);

    if (!r2.ok) {
        alert('更新 tags_pending.json 失败: ' + (r2.message || ''));
    }

    // 刷新标签库与待定列表
    await loadKnownTags();
    await loadPendingTags();
    render();
}
