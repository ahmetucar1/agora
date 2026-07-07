const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const DATA_FILE = path.join(ROOT, 'data', 'products.json');

function sendJson(res, statusCode, payload, headers = {}) {
  res.statusCode = statusCode;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  Object.entries(headers).forEach(([key, value]) => {
    res.setHeader(key, value);
  });
  res.end(JSON.stringify(payload));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf-8')));
    req.on('error', reject);
  });
}

async function readJsonBody(req) {
  const raw = await readBody(req);
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function loadLocalProducts() {
  try {
    const raw = fs.readFileSync(DATA_FILE, 'utf-8');
    const parsed = JSON.parse(raw);
    if (parsed && Array.isArray(parsed.categories)) {
      return parsed;
    }
  } catch {
    // ignore
  }
  return { categories: [] };
}

function supabaseConfig() {
  const url = process.env.SUPABASE_URL || '';
  const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY || '';
  const available = Boolean(url && serviceKey);
  return { url, serviceKey, available };
}

async function supabaseRequest(pathname, options = {}) {
  const { url, serviceKey, available } = supabaseConfig();
  if (!available) {
    throw new Error('Supabase config missing');
  }

  const response = await fetch(`${url}${pathname}`, {
    ...options,
    headers: {
      apikey: serviceKey,
      Authorization: `Bearer ${serviceKey}`,
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });

  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }

  if (!response.ok) {
    const message = typeof data === 'object' && data && data.message
      ? data.message
      : `Supabase request failed (${response.status})`;
    const error = new Error(message);
    error.status = response.status;
    error.data = data;
    throw error;
  }

  return data;
}

function normalizeMenu(categories) {
  return {
    categories: (categories || []).map((category) => ({
      id: category.id,
      name: category.name,
      image: category.image,
      products: (category.products || [])
        .slice()
        .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0))
        .map((product) => ({
          id: product.id,
          name: product.name,
          price: Number(product.price || 0),
          image: product.image || null,
        })),
    })),
  };
}

module.exports = {
  loadLocalProducts,
  normalizeMenu,
  readJsonBody,
  sendJson,
  supabaseConfig,
  supabaseRequest,
};
