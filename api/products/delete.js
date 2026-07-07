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
    const id = String(payload.id || '').trim();
    if (!id) {
      return sendJson(res, 400, { ok: false, error: 'Ürün ID zorunlu' });
    }

    await supabaseRequest(`/rest/v1/products?id=eq.${encodeURIComponent(id)}`, {
      method: 'DELETE',
    });

    return sendJson(res, 200, { ok: true, message: 'Ürün silindi' });
  } catch (error) {
    return sendJson(res, error.status || 500, { ok: false, error: error.message || 'Ürün silinemedi.' });
  }
};
