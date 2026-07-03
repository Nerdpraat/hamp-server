const { verifyToken } = require('./jwt');

function extractToken(req) {
  const header = req.headers.authorization || '';
  if (header.startsWith('Bearer ')) return header.slice(7);
  return null;
}

async function authRequired(req, res, next) {
  const token = extractToken(req);
  if (!token) {
    return res.status(401).json({ error: 'Missing or invalid Authorization header' });
  }
  try {
    const decoded = verifyToken(token);
    req.user = { id: decoded.sub, role: decoded.role, email: decoded.email };
    next();
  } catch {
    return res.status(401).json({ error: 'Invalid or expired token' });
  }
}

function adminRequired(req, res, next) {
  if (!req.user || req.user.role !== 'admin') {
    return res.status(403).json({ error: 'Admin access required' });
  }
  next();
}

function signUserPayload(user) {
  return { sub: user.id, role: user.role, email: user.email };
}

module.exports = { authRequired, adminRequired, signUserPayload, extractToken };
