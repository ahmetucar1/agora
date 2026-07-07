const { readJsonBody, sendJson, supabaseConfig, supabaseRequest } = require('../_shared');

async function ensureCategory(payload) {
  const categoryMode = String(payload.category_mode || 'existing');
  const selected = categoryMode === 'new' ? String(payload.new_category_name || '').trim() : String(payload.category || '').trim();
  if (!selected) {
    throw new Error('Kategori adı zorunlu');
  }

  const found = await supabaseRequest(`/rest/v1/categories?select=id,name,image&name=eq.${encodeURIComponent(selected)}&limit=1`);
  if (Array.isArray(found) && found.length) {
    return found[0];
  }

  const inserted = await supabaseRequest('/rest/v1/categories', {
    method: 'POST',
    headers: { Prefer: 'return=representation' },
    body: JSON.stringify([{ name: selected, image: 'sıcakicecek.jpg' }]),
  });

  return inserted[0];
}

module.exports = async function handler(req, res) {
  if (req.method !== 'POST') {
    return sendJson(res, 405, { ok: false, error: 'Method not allowed' });
  }

  if (!supabaseConfig().available) {
    return sendJson(res, 503, { ok: false, error: 'Yazma işlemi için Supabase env değişkenleri gerekli.' });
  }

  try {
    const payload = await readJsonBody(req);
    const name = String(payload.name || '').trim();
    const price = Number(payload.price || 0);

    if (!name) {
      return sendJson(res, 400, { ok: false, error: 'Ürün adı zorunlu' });
    }

    const category = await ensureCategory(payload);

    const rows = await supabaseRequest('/rest/v1/products', {
      method: 'POST',
      headers: { Prefer: 'return=representation' },
      body: JSON.stringify([
        {
          name,
          price: Number.isFinite(price) ? price : 0,
          image: null,
          category_id: category.id,
        },
      ]),
    });

    const product = rows[0];
    return sendJson(res, 200, {
      ok: true,
      message: 'Ürün başarıyla eklendi',
      product: {
        id: product.id,
        name: product.name,
        price: Number(product.price || 0),
        image: product.image,
        category: category.name,
      },
    });
  } catch (error) {
    return sendJson(res, error.status || 500, { ok: false, error: error.message || 'İşlem başarısız oldu.' });
  }
};
