/**
 * GitHub Client
 * 封装与 GitHub REST API 的交互，供收藏/深度分析功能使用：
 *  - 触发 repository_dispatch（启动后端深度分析 workflow）
 *  - 通过 Contents API 读取 data 分支文件（读最新，绕过 raw CDN 缓存）
 *  - 通过 Contents API 写入/更新 data 分支文件（base64 + UTF-8，sha 乐观锁重试）
 *
 * 依赖 DATA_CONFIG（js/data-config.js）提供 repoOwner / repoName / dataBranch。
 * PAT 存于 localStorage['github_pat']，为 fine-grained token（仅本仓库 Contents:RW）。
 */

const GitHubClient = {
    PAT_KEY: 'github_pat',
    API_BASE: 'https://api.github.com',
    DEFAULT_MAX_RETRIES: 8,
    _writeQueues: {},

    /** 读取 PAT */
    getToken: function() {
        return (localStorage.getItem(this.PAT_KEY) || '').trim();
    },

    /** 是否已配置 PAT */
    hasToken: function() {
        return this.getToken().length > 0;
    },

    _owner: function() { return DATA_CONFIG.repoOwner; },
    _repo: function() { return DATA_CONFIG.repoName; },
    _branch: function() { return DATA_CONFIG.dataBranch; },

    _headers: function() {
        return {
            'Authorization': `Bearer ${this.getToken()}`,
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28'
        };
    },

    /** UTF-8 字符串 -> base64（兼容中文） */
    _toBase64: function(str) {
        return btoa(unescape(encodeURIComponent(str)));
    },

    /** base64 -> UTF-8 字符串 */
    _fromBase64: function(b64) {
        return decodeURIComponent(escape(atob(b64.replace(/\n/g, ''))));
    },

    /**
     * 校验 PAT 是否可用（GET 仓库信息）。
     * @returns {Promise<{ok:boolean, status:number, message?:string}>}
     */
    testToken: async function() {
        if (!this.hasToken()) {
            return { ok: false, status: 0, message: '未配置 PAT' };
        }
        try {
            const resp = await fetch(`${this.API_BASE}/repos/${this._owner()}/${this._repo()}`, {
                headers: this._headers()
            });
            if (resp.ok) return { ok: true, status: resp.status };
            const body = await resp.json().catch(() => ({}));
            return { ok: false, status: resp.status, message: body.message || resp.statusText };
        } catch (e) {
            return { ok: false, status: 0, message: e.message };
        }
    },

    /**
     * 触发 repository_dispatch，启动深度分析 workflow。
     * @param {string} eventType 事件类型，如 'deep-analyze'
     * @param {object} payload client_payload
     * @returns {Promise<{ok:boolean, status:number, message?:string}>}
     */
    dispatch: async function(eventType, payload) {
        if (!this.hasToken()) {
            return { ok: false, status: 0, message: '未配置 PAT' };
        }
        try {
            const resp = await fetch(
                `${this.API_BASE}/repos/${this._owner()}/${this._repo()}/dispatches`,
                {
                    method: 'POST',
                    headers: this._headers(),
                    body: JSON.stringify({ event_type: eventType, client_payload: payload })
                }
            );
            // dispatches 成功返回 204 No Content
            if (resp.status === 204) return { ok: true, status: 204 };
            const body = await resp.json().catch(() => ({}));
            return { ok: false, status: resp.status, message: body.message || resp.statusText };
        } catch (e) {
            return { ok: false, status: 0, message: e.message };
        }
    },

    /**
     * 通过 Contents API 读取 data 分支上的文件。
     * @param {string} path 仓库内路径，如 'data/deep/2606.26157.json'
     * @returns {Promise<{exists:boolean, content?:string, sha?:string, status:number}>}
     */
    getFile: async function(path) {
        try {
            // 仅靠 URL 时间戳 + cache:'no-store' 绕过缓存。
            // 不要再追加 Cache-Control / Pragma / If-None-Match 这类自定义头，
            // 否则 PAT(Bearer) + 自定义头 会触发 CORS 预检，
            // 某些网络/中间层下预检会被拦截，导致 fetch 直接 reject，
            // 表现为 read-modify-write 8 次重试全部 "读取远端文件失败 (network)"。
            const url = `${this.API_BASE}/repos/${this._owner()}/${this._repo()}/contents/${path}?ref=${this._branch()}&_ts=${Date.now()}`;
            const headers = this.hasToken() ? this._headers() : { 'Accept': 'application/vnd.github+json' };
            const resp = await fetch(url, { headers, cache: 'no-store' });
            if (resp.status === 404) return { exists: false, status: 404 };
            if (!resp.ok) return { exists: false, status: resp.status };
            const body = await resp.json();
            return {
                exists: true,
                status: 200,
                sha: body.sha,
                content: body.content ? this._fromBase64(body.content) : ''
            };
        } catch (e) {
            return { exists: false, status: 0, message: e && e.message ? e.message : 'network error' };
        }
    },

    /**
     * 通过 Contents API 写入/更新文件（PUT）。
     * @param {string} path 路径
     * @param {string} contentStr 文件文本内容
     * @param {string} message commit message
     * @param {string|null} sha 更新时必填的当前 sha；新建时传 null
     * @returns {Promise<{ok:boolean, status:number, sha?:string, message?:string}>}
     */
    putFile: async function(path, contentStr, message, sha) {
        if (!this.hasToken()) {
            return { ok: false, status: 0, message: '未配置 PAT' };
        }
        const body = {
            message: message,
            content: this._toBase64(contentStr),
            branch: this._branch()
        };
        if (sha) body.sha = sha;
        try {
            const resp = await fetch(
                `${this.API_BASE}/repos/${this._owner()}/${this._repo()}/contents/${path}`,
                { method: 'PUT', headers: this._headers(), body: JSON.stringify(body) }
            );
            if (resp.ok) {
                const data = await resp.json().catch(() => ({}));
                return { ok: true, status: resp.status, sha: data.content && data.content.sha };
            }
            const errBody = await resp.json().catch(() => ({}));
            return { ok: false, status: resp.status, message: errBody.message || resp.statusText };
        } catch (e) {
            return { ok: false, status: 0, message: e.message };
        }
    },

    /**
     * read-modify-write，带 sha 乐观锁与指数退避重试。
     * 适用于多处追加的共享小文件（tags.json / tags_pending.json / favorites.jsonl）。
     * @param {string} path 路径
     * @param {function(string|null):string} mutateFn 接收当前文件内容(不存在为 null)，返回新内容
     * @param {string} message commit message
     * @param {number} maxRetries 最大重试次数
     * @returns {Promise<{ok:boolean, message?:string}>}
     */
    _sleep: function(ms) {
        return new Promise(r => setTimeout(r, ms));
    },

    _conflictBackoffMs: function(attempt) {
        const base = Math.min(8000, 500 * Math.pow(1.7, attempt));
        const jitter = Math.floor(Math.random() * 350);
        return base + jitter;
    },

    _isConflictLike: function(res) {
        if (!res) return false;
        if (res.status === 409) return true;
        // Contents API 在 sha 过期、分支刚被其他提交更新、或 create/update 判断不一致时可能返回 422。
        // 仅把看起来像 sha/ref 冲突的 422 当作可重试错误，避免吞掉真正的参数错误。
        const msg = (res.message || '').toLowerCase();
        return res.status === 422 && (
            msg.includes('sha') ||
            msg.includes('reference') ||
            msg.includes('update') ||
            msg.includes('already exists') ||
            msg.includes('does not match')
        );
    },

    _enqueueFileUpdate: function(path, task) {
        const previous = this._writeQueues[path] || Promise.resolve();
        const next = previous.catch(() => {}).then(task);
        this._writeQueues[path] = next.finally(() => {
            if (this._writeQueues[path] === next) delete this._writeQueues[path];
        });
        return next;
    },

    updateFileWithRetry: async function(path, mutateFn, message, maxRetries = this.DEFAULT_MAX_RETRIES) {
        return this._enqueueFileUpdate(path, () => this._updateFileWithRetryNow(path, mutateFn, message, maxRetries));
    },

    _extractLatestShaFromMessage: function(message) {
        if (!message) return null;
        const m = String(message).match(/does not match\s+([0-9a-f]{7,40})/i);
        return m ? m[1] : null;
    },

    _updateFileWithRetryNow: async function(path, mutateFn, message, maxRetries) {
        let lastMessage = '';
        let overrideSha = null;
        let overrideContent = null;
        for (let attempt = 0; attempt < maxRetries; attempt++) {
            let currentSha = overrideSha;
            let oldContent = overrideContent;
            if (currentSha === null) {
                const current = await this.getFile(path);
                if (!current.exists && current.status !== 404) {
                    lastMessage = `读取远端文件失败 (${current.status || 'network'})`;
                    await this._sleep(this._conflictBackoffMs(attempt));
                    continue;
                }
                oldContent = current.exists ? current.content : null;
                currentSha = current.sha || null;
            }
            overrideSha = null;
            overrideContent = null;
            const newContent = mutateFn(oldContent);
            // 内容未变化则无需写入
            if (oldContent !== null && newContent === oldContent) {
                return { ok: true };
            }
            const res = await this.putFile(path, newContent, message, currentSha);
            if (res.ok) return { ok: true };
            lastMessage = res.message || `HTTP ${res.status}`;
            // 409 / 部分 422 是 sha 冲突：重新读取最新 sha，再用 mutateFn 合并一次后写入。
            if (this._isConflictLike(res)) {
                // GitHub 422 "does not match <sha>" 错误里直接带着最新 sha：
                // 取出后立刻再读一次最新内容，避免被任何缓存命中旧 sha。
                const latestSha = this._extractLatestShaFromMessage(res.message);
                if (latestSha && latestSha !== currentSha) {
                    const refreshed = await this.getFile(path);
                    if (refreshed.exists && refreshed.sha === latestSha) {
                        overrideSha = refreshed.sha;
                        overrideContent = refreshed.content;
                    }
                }
                await this._sleep(this._conflictBackoffMs(attempt));
                continue;
            }
            return { ok: false, message: res.message };
        }

        // 最后再读一次：如果最新内容已经等于本次 mutate 期望值，说明别的重试/标签页已经写成功。
        const latest = await this.getFile(path);
        if (latest.exists) {
            try {
                if (mutateFn(latest.content) === latest.content) return { ok: true };
            } catch (e) { /* ignore final verification errors */ }
        }
        return { ok: false, message: `写入冲突，已自动重试 ${maxRetries} 次仍未成功${lastMessage ? `（最后错误：${lastMessage}）` : ''}` };
    }
};
