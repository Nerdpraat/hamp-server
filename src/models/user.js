const { pool } = require('../db/pool');

async function findByEmail(email) {
  const [rows] = await pool.query('SELECT * FROM users WHERE email = ? LIMIT 1', [email]);
  return rows[0] || null;
}

async function findById(id) {
  const [rows] = await pool.query('SELECT * FROM users WHERE id = ? LIMIT 1', [id]);
  return rows[0] || null;
}

async function create({ email, passwordHash, name, role = 'user' }) {
  const [result] = await pool.query(
    'INSERT INTO users (email, password_hash, name, role) VALUES (?, ?, ?, ?)',
    [email, passwordHash, name || null, role]
  );
  return findById(result.insertId);
}

async function countAll() {
  const [rows] = await pool.query('SELECT COUNT(*) AS c FROM users');
  return Number(rows[0].c);
}

async function listAll() {
  const [rows] = await pool.query('SELECT id, email, name, role, created_at FROM users ORDER BY id ASC');
  return rows;
}

function sanitize(user) {
  if (!user) return null;
  const { password_hash: _password_hash, ...rest } = user;
  return rest;
}

module.exports = { findByEmail, findById, create, countAll, listAll, sanitize };
