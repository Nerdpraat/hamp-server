const { Router } = require('express');
const z = require('zod');
const rateLimit = require('express-rate-limit');

const User = require('../models/user');
const { hashPassword, verifyPassword } = require('../auth/password');
const { signToken } = require('../auth/jwt');
const { signUserPayload, authRequired } = require('../auth/middleware');

const router = Router();

const loginLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 30,
  standardHeaders: true,
  legacyHeaders: false,
});

const registerSchema = z.object({
  email: z.string().email().max(255),
  password: z.string().min(8).max(128),
  name: z.string().max(120).optional(),
});

const loginSchema = z.object({
  email: z.string().email(),
  password: z.string().min(1),
});

router.post('/register', async (req, res, next) => {
  try {
    const parsed = registerSchema.safeParse(req.body);
    if (!parsed.success) {
      return res.status(400).json({ error: 'Invalid input', details: parsed.error.flatten() });
    }
    const { email, password, name } = parsed.data;

    const existing = await User.findByEmail(email);
    if (existing) {
      return res.status(409).json({ error: 'Email already registered' });
    }

    const count = await User.countAll();
    // First registered user becomes admin (bootstrap).
    const role = count === 0 ? 'admin' : 'user';
    const passwordHash = await hashPassword(password);
    const user = await User.create({ email, passwordHash, name, role });
    const token = signToken(signUserPayload(user));
    res.status(201).json({ token, user: User.sanitize(user) });
  } catch (err) {
    next(err);
  }
});

router.post('/login', loginLimiter, async (req, res, next) => {
  try {
    const parsed = loginSchema.safeParse(req.body);
    if (!parsed.success) {
      return res.status(400).json({ error: 'Invalid input' });
    }
    const { email, password } = parsed.data;
    const user = await User.findByEmail(email);
    if (!user) {
      return res.status(401).json({ error: 'Invalid credentials' });
    }
    const ok = await verifyPassword(password, user.password_hash);
    if (!ok) {
      return res.status(401).json({ error: 'Invalid credentials' });
    }
    const token = signToken(signUserPayload(user));
    res.json({ token, user: User.sanitize(user) });
  } catch (err) {
    next(err);
  }
});

router.get('/me', authRequired, async (req, res, next) => {
  try {
    const user = await User.findById(req.user.id);
    if (!user) return res.status(404).json({ error: 'User not found' });
    res.json({ user: User.sanitize(user) });
  } catch (err) {
    next(err);
  }
});

module.exports = router;
