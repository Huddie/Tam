-- Adds a required, human-chosen name to every publishing token (unique per
-- user, so e.g. "colab" and "laptop" can't collide for the same person, but
-- two different people can both have a token named "colab"). Existing rows
-- get backfilled deterministically -- 'my-token' for a user's oldest token,
-- 'my-token-2'/'my-token-3'/... for any others they already had -- rather
-- than assuming today's data only ever has one token per user, since a
-- migration shouldn't depend on that being true.

ALTER TABLE tokens ADD COLUMN name TEXT;

UPDATE tokens SET name = (
  SELECT CASE WHEN ranked.rn = 1 THEN 'my-token' ELSE 'my-token-' || ranked.rn END
  FROM (
    SELECT id, ROW_NUMBER() OVER (PARTITION BY user ORDER BY created_at) AS rn
    FROM tokens
  ) ranked
  WHERE ranked.id = tokens.id
);

CREATE UNIQUE INDEX idx_tokens_user_name ON tokens(user, name);
