const { Router } = require('express');

const User = require('../models/user');
const { authRequired, adminRequired } = require('../auth/middleware');

const router = Router();

// List all users (admin only). Used by the admin dashboard to populate the
// site owner dropdown.
router.get('/', authRequired, adminRequired, async (req, res, next) => {
  try {
    const users = await User.listAll();
    res.json({ users: users.map(User.sanitize) });
  } catch (err) {
    next(err);
  }
});

module.exports = router;
