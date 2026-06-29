/**
 * Favorites Core
 * 收藏功能的核心逻辑：基于 localStorage 管理收藏的论文 id 列表。
 * 被主页 (app.js) 与收藏夹页 (favorites-page.js) 共用。
 */

const Favorites = {
    STORAGE_KEY: 'favorited_paper_ids',
    META_KEY: 'favorited_papers_meta',

    /**
     * 读取收藏的论文 id 数组
     * @returns {string[]}
     */
    getIds: function() {
        try {
            const raw = localStorage.getItem(this.STORAGE_KEY);
            if (!raw) return [];
            const ids = JSON.parse(raw);
            return Array.isArray(ids) ? ids : [];
        } catch (e) {
            console.error('解析收藏列表失败:', e);
            return [];
        }
    },

    /** 读取收藏的元数据 map: { id: {title,date,pdf,abs,categories} } */
    getMeta: function() {
        try {
            const raw = localStorage.getItem(this.META_KEY);
            if (!raw) return {};
            const meta = JSON.parse(raw);
            return (meta && typeof meta === 'object') ? meta : {};
        } catch (e) {
            return {};
        }
    },

    _saveMeta: function(meta) {
        localStorage.setItem(this.META_KEY, JSON.stringify(meta));
    },

    /** 取某篇收藏的元数据 */
    metaOf: function(id) {
        return this.getMeta()[id] || null;
    },

    /**
     * 是否已收藏
     * @param {string} id 论文 id
     * @returns {boolean}
     */
    has: function(id) {
        if (!id) return false;
        return this.getIds().includes(id);
    },

    /**
     * 写入收藏 id 数组
     * @param {string[]} ids
     */
    _save: function(ids) {
        localStorage.setItem(this.STORAGE_KEY, JSON.stringify(ids));
    },

    /**
     * 添加收藏
     * @param {string} id
     * @param {object} [meta] 论文元数据 {title,date,pdf,abs,categories}
     * @returns {boolean} 是否新增（已存在返回 false）
     */
    add: function(id, meta) {
        if (!id) return false;
        if (meta) {
            const all = this.getMeta();
            all[id] = meta;
            this._saveMeta(all);
        }
        const ids = this.getIds();
        if (ids.includes(id)) return false;
        ids.push(id);
        this._save(ids);
        return true;
    },

    /**
     * 取消收藏
     * @param {string} id
     * @returns {boolean} 是否移除（不存在返回 false）
     */
    remove: function(id) {
        if (!id) return false;
        const meta = this.getMeta();
        if (meta[id]) {
            delete meta[id];
            this._saveMeta(meta);
        }
        const ids = this.getIds();
        const idx = ids.indexOf(id);
        if (idx === -1) return false;
        ids.splice(idx, 1);
        this._save(ids);
        return true;
    },

    /**
     * 切换收藏状态
     * @param {string} id
     * @param {object} [meta] 论文元数据
     * @returns {boolean} 切换后是否为已收藏
     */
    toggle: function(id, meta) {
        if (this.has(id)) {
            this.remove(id);
            return false;
        }
        this.add(id, meta);
        return true;
    },

    /**
     * 用一组 id 合并进本地收藏（用于跨设备从 favorites.jsonl 恢复）
     * @param {string[]} ids
     */
    merge: function(ids) {
        if (!Array.isArray(ids) || ids.length === 0) return;
        const current = this.getIds();
        const set = new Set(current);
        ids.forEach(id => { if (id) set.add(id); });
        this._save(Array.from(set));
    },

    /** 解析远端 favorites.jsonl */
    parseRemoteRows: function(content) {
        const rows = [];
        if (!content) return rows;
        content.split('\n').forEach(line => {
            if (!line.trim()) return;
            try { rows.push(JSON.parse(line)); } catch (e) { /* ignore malformed row */ }
        });
        return rows;
    },

    /** 把远端 favorites.jsonl 中的一行合并为本地元数据，保留本机手动编辑过的标签/摘要。
     *  对于 affiliation_type / is_industrial_paper / org_display / industry_orgs / authors 等
     *  AI 增强字段：远端如果给出了"有意义的值"（非空、非 unknown），优先采用远端，
     *  避免上一轮失败留在本地的 unknown/空值挡住新的增强结果。
     */
    remoteRowToMeta: function(row, currentMeta = {}) {
        const id = row.id;
        const rowCategories = Array.isArray(row.categories) ? row.categories : (row.categories ? [row.categories] : []);

        // 远端是否给出了"实质值"：空字符串、null、undefined 不算
        const remoteHas = (v) => v !== undefined && v !== null && v !== '';
        // affiliation_type / org_display 等 AI 字段：本地是 unknown/空就用远端
        const preferRemoteStr = (remoteVal, localVal, localUnknownTokens = []) => {
            if (!remoteHas(remoteVal)) return localVal || '';
            const localStr = (localVal || '').toString().trim().toLowerCase();
            if (!localStr || localUnknownTokens.includes(localStr)) return remoteVal;
            return localVal;
        };
        // bool 字段：本地若是 false 且远端是 true，则采用远端（前提是远端给了字段）
        const preferRemoteBool = (remoteVal, localVal) => {
            if (typeof remoteVal !== 'boolean') {
                return (typeof localVal === 'boolean') ? localVal : !!remoteVal;
            }
            if (typeof localVal !== 'boolean') return remoteVal;
            return localVal || remoteVal;
        };

        return {
            ...currentMeta,
            title: currentMeta.title || row.title || id,
            date: currentMeta.date || row.date || '',
            pdf: currentMeta.pdf || row.pdf || `https://arxiv.org/pdf/${id}`,
            abs: currentMeta.abs || row.abs || `https://arxiv.org/abs/${id}`,
            categories: (Array.isArray(currentMeta.categories) && currentMeta.categories.length > 0)
                ? currentMeta.categories
                : rowCategories,
            authors: preferRemoteStr(row.authors, currentMeta.authors),
            details: preferRemoteStr(row.details, currentMeta.details),
            // bool：本地 false 不应该挡住远端 true
            is_ab_test: preferRemoteBool(row.is_ab_test, currentMeta.is_ab_test),
            is_industrial_paper: preferRemoteBool(row.is_industrial_paper, currentMeta.is_industrial_paper),
            // 字符串型 AI 字段：本地 unknown/空时让远端胜出
            affiliation_type: preferRemoteStr(row.affiliation_type, currentMeta.affiliation_type, ['unknown']),
            org_display: preferRemoteStr(row.org_display, currentMeta.org_display),
            industry_orgs: preferRemoteStr(row.industry_orgs, currentMeta.industry_orgs),
            code_url: preferRemoteStr(row.code_url, currentMeta.code_url),
            code_stars: (currentMeta.code_stars && currentMeta.code_stars > 0) ? currentMeta.code_stars : (row.code_stars || 0),
            tags: currentMeta.tagsEditedLocally
                ? (currentMeta.tags || [])
                : (Array.isArray(row.tags) ? row.tags : (currentMeta.tags || [])),
            summary: currentMeta.summaryEditedLocally
                ? (currentMeta.summary || '')
                : (row.summary || currentMeta.summary || '')
        };
    },

    /** 把本地元数据序列化为远端 favorites.jsonl 的一行。 */
    metaToRemoteRow: function(id, meta = {}, existingRow = {}) {
        return {
            id,
            title: meta.title || existingRow.title || id,
            date: meta.date || existingRow.date || '',
            abs: meta.abs || existingRow.abs || `https://arxiv.org/abs/${id}`,
            pdf: meta.pdf || existingRow.pdf || `https://arxiv.org/pdf/${id}`,
            authors: meta.authors || existingRow.authors || '',
            categories: Array.isArray(meta.categories) ? meta.categories : (existingRow.categories || []),
            details: meta.details || existingRow.details || '',
            is_ab_test: typeof meta.is_ab_test === 'boolean' ? meta.is_ab_test : !!existingRow.is_ab_test,
            is_industrial_paper: typeof meta.is_industrial_paper === 'boolean' ? meta.is_industrial_paper : !!existingRow.is_industrial_paper,
            affiliation_type: meta.affiliation_type || existingRow.affiliation_type || 'unknown',
            org_display: meta.org_display || existingRow.org_display || '',
            industry_orgs: meta.industry_orgs || existingRow.industry_orgs || '',
            code_url: meta.code_url || existingRow.code_url || '',
            code_stars: meta.code_stars || existingRow.code_stars || 0,
            tags: Array.isArray(meta.tags) ? meta.tags : (Array.isArray(existingRow.tags) ? existingRow.tags : []),
            summary: meta.summary || existingRow.summary || '',
            has_deep: !!(meta.has_deep || existingRow.has_deep),
            favorited_at: existingRow.favorited_at || new Date().toISOString(),
        };
    },

    /** 从 data 分支 favorites.jsonl 恢复收藏到本地（跨浏览器/跨设备）。 */
    restoreFromRemote: async function() {
        if (typeof GitHubClient === 'undefined') return { ok: false, count: 0, message: 'GitHubClient unavailable' };
        const res = await GitHubClient.getFile('data/favorites.jsonl');
        if (!res.exists || !res.content) return { ok: true, count: 0 };
        const ids = [];
        const metaAll = this.getMeta();
        this.parseRemoteRows(res.content).forEach(row => {
            if (!row.id) return;
            ids.push(row.id);
            metaAll[row.id] = this.remoteRowToMeta(row, metaAll[row.id] || {});
        });
        this._saveMeta(metaAll);
        this.merge(ids);
        return { ok: true, count: ids.length };
    },

    /**
     * 把本地收藏整体回填到远端 favorites.jsonl。
     * 解决旧浏览器里已有的收藏（开启同步前收藏）在新浏览器不可见的问题。
     */
    syncAllToRemote: async function() {
        if (typeof GitHubClient === 'undefined' || !GitHubClient.hasToken()) {
            return { ok: false, message: '未配置 PAT' };
        }
        const ids = this.getIds();
        const metaAll = this.getMeta();
        if (ids.length === 0) return { ok: true };
        return GitHubClient.updateFileWithRetry('data/favorites.jsonl', (old) => {
            const rows = this.parseRemoteRows(old);
            const byId = new Map(rows.filter(r => r.id).map(r => [r.id, r]));
            ids.forEach(id => {
                byId.set(id, this.metaToRemoteRow(id, metaAll[id] || {}, byId.get(id) || {}));
            });
            return Array.from(byId.values()).map(r => JSON.stringify(r)).join('\n') + '\n';
        }, 'favorites: sync local index');
    }
};
