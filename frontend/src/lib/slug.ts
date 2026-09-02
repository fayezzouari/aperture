/**
 * Slug derivation, shared by every "create a thing" dialog.
 *
 * A slug is API surface — it is what products put in their URLs and it cannot
 * change afterwards — so it is worth getting the small details right: accents
 * folded rather than dropped, runs of punctuation collapsed to one hyphen, and
 * the caller's own edits respected once they start typing.
 */

/** Matches the pattern the API enforces for model and workflow slugs. */
export const SLUG_PATTERN = /^[a-z0-9][a-z0-9._-]{1,94}$/;

export const SLUG_MAX = 95;

/** "Rear bumper damage" -> "rear-bumper-damage"; "Fahrzeugtür" -> "fahrzeugtur". */
export function slugify(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, SLUG_MAX);
}

/**
 * For a slug the user is typing by hand: keep what they meant, drop what the API
 * would reject. A trailing hyphen survives, because removing it mid-word would
 * fight the person typing.
 */
export function sanitizeSlug(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/\s+/g, "-")
    .replace(/[^a-z0-9._-]/g, "")
    .replace(/^[^a-z0-9]+/, "")
    .slice(0, SLUG_MAX);
}

/** null when the slug is usable, otherwise why it is not. */
export function slugProblem(value: string): string | null {
  if (!value) return null;
  if (value.length < 2) return "A slug needs at least two characters.";
  if (!SLUG_PATTERN.test(value)) {
    return "Use lowercase letters, digits, dot, dash or underscore, starting with a letter or digit.";
  }
  return null;
}
