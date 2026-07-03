const fs = require('fs');
const path = require('path');
const { pool } = require('./pool');

async function migrate() {
  const sql = fs.readFileSync(path.join(__dirname, 'schema.sql'), 'utf8');
  const statements = sql
    .split(/;\s*\n/)
    .map((s) => s.trim())
    .filter(Boolean);

  const conn = await pool.getConnection();
  try {
    await conn.query('SET FOREIGN_KEY_CHECKS=0');
    for (const stmt of statements) {
      await conn.query(stmt);
    }
    await conn.query('SET FOREIGN_KEY_CHECKS=1');
    console.log(`Migration complete: ${statements.length} statements executed.`);
  } finally {
    conn.release();
  }
}

if (require.main === module) {
  migrate()
    .then(() => process.exit(0))
    .catch((err) => {
      console.error('Migration failed:', err);
      process.exit(1);
    });
}

module.exports = { migrate };
