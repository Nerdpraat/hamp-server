const z = require('zod');

const readingSchema = z.object({
  entityId: z.string().min(1).max(255),
  value: z.number().finite(),
  unit: z.string().max(16).optional(),
  ts: z.union([z.number(), z.string()]).optional(),
});

function normalize(r, siteId) {
  let recordedAt;
  if (r.ts !== undefined && r.ts !== null && r.ts !== '') {
    const d = new Date(r.ts);
    recordedAt = isNaN(d.getTime()) ? undefined : d;
  }
  return {
    siteId,
    entityId: r.entityId,
    value: r.value,
    unit: r.unit || 'W',
    recordedAt,
  };
}

module.exports = { readingSchema, normalize };
