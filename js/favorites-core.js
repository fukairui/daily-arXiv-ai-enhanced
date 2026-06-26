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
    }
};
