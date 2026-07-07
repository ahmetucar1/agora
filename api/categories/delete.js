const { readJsonBody, sendJson, supabaseConfig, supabaseRequest } = require('../_shared');

module.exports = async function handler(req, res) {
  if (req.method !== 'POST') {
    return sendJson(res, 405, { ok: false, error: 'Method not allowed' });
  }

  if (!supabaseConfig().available) {
    return sendJson(res, 503, { ok: false, error: 'Yazma işlemi için Supabase env değişkenleri gerekli.' });
  }

  try {
    const payload = await readJsonBody(req);
    const requestedName = String(payload.name || payload.id || '').trim();
    if (!requestedName) {
      return sendJson(res, 400, { ok: false, error: 'Kategori adı zorunlu' });
    }

    const categories = await supabaseRequest(`/rest/v1/categories?select=id,name&name=eq.${encodeURIComponent(requestedName)}&limit=1`);
    if (!Array.isArray(categories) || !categories.length) {
      return sendJson(res, 404, { ok: false, error: 'Kategori bulunamadı' });
    }

    const category = categories[0];

    await supabaseRequest(`/rest/v1/products?category_id=eq.${encodeURIComponent(category.id)}`, {
      method: 'DELETE',
    });

    await supabaseRequest(`/rest/v1/categories?id=eq.${encodeURIComponent(category.id)}`, {
      method: 'DELETE',
    });

    return sendJson(res, 200, { ok: true, message: 'Kategori silindi' });
  } catch (error) {
    return sendJson(res, error.status || 500, { ok: false, error: error.message || 'Kategori silinemedi.' });
  }
};
