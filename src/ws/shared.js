const z = require('zod');

const readingSchema = z.object({
  entityId: z.string().min(1).max(255),
  value: z.number().finite(),
  unit: z.string().max(16).optional(),
  ts: z.union([z.number(), z.string()]).optional(),
});

function normalize(r, siteId) {
  return {
    siteId,
    entityId: r.entityId,
    value: r.value,
    unit: r.unit || 'W',
    recordedAt: r.ts,
  };
}

module.exports = { readingSchema, normalize };
