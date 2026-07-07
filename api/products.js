const { loadLocalProducts, normalizeMenu, sendJson, supabaseConfig, supabaseRequest } = require('./_shared');

module.exports = async function handler(req, res) {
  if (req.method !== 'GET') {
    return sendJson(res, 405, { ok: false, error: 'Method not allowed' });
  }

  const { available } = supabaseConfig();

  if (!available) {
    return sendJson(
      res,
      200,
      loadLocalProducts(),
      {
        'x-agora-api-mode': 'json',
        'x-agora-writable': 'false',
      }
    );
  }

  try {
    const categories = await supabaseRequest(
      '/rest/v1/categories?select=id,name,image,sort_order,products(id,name,price,image,sort_order)&order=sort_order.asc&products.order=sort_order.asc'
    );

    return sendJson(
      res,
      200,
      normalizeMenu(categories),
      {
        'x-agora-api-mode': 'json',
        'x-agora-writable': 'true',
      }
    );
  } catch (error) {
    return sendJson(
      res,
      200,
      loadLocalProducts(),
      {
        'x-agora-api-mode': 'json',
        'x-agora-writable': 'false',
      }
    );
  }
};
