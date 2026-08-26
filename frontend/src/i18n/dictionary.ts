/**
 * The dictionary shape, in its own module so the locale files and the provider can
 * both import it without the locale files importing the provider (and with it React).
 */

export type Dictionary = Record<string, string>;
