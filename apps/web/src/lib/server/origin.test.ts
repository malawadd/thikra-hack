import { describe, expect, it } from 'vitest';
import { isTrustedMutationOrigin } from './origin';

describe('isTrustedMutationOrigin', () => {
  it('accepts the request URL origin and requests without an Origin header', () => {
    expect(isTrustedMutationOrigin(null, 'http://localhost:43191')).toBe(true);
    expect(
      isTrustedMutationOrigin('http://localhost:43191', 'http://localhost:43191')
    ).toBe(true);
  });

  it('accepts an exact configured tunnel origin behind an internal host', () => {
    expect(
      isTrustedMutationOrigin(
        'https://thikratest.mukaeb.com',
        'http://localhost:43191',
        'https://thikratest.mukaeb.com'
      )
    ).toBe(true);
  });

  it('rejects sibling domains, URL paths, and invalid configured values', () => {
    expect(
      isTrustedMutationOrigin(
        'https://evil.mukaeb.com',
        'http://localhost:43191',
        'https://thikratest.mukaeb.com'
      )
    ).toBe(false);
    expect(
      isTrustedMutationOrigin(
        'https://thikratest.mukaeb.com',
        'http://localhost:43191',
        'https://thikratest.mukaeb.com/path,not-a-url'
      )
    ).toBe(false);
  });
});

