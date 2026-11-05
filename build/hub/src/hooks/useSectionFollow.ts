import { useEffect } from 'react';

/**
 * Section anchors: manifest section keys -> the `id` the page renders for them.
 *
 * Declared sections are reachable by URL (`/play?section=Controllers`). The
 * upstream HubPage did that for the pages it owned; Rowyn's own pages render
 * plain sections, so they follow the same query themselves. One shared hook
 * keeps every page on the same rule instead of each one re-implementing it.
 */
export type SectionMap = Record<string, string>;

export function sectionAnchor(section: string | null | undefined, map: SectionMap): string | null {
  if (!section || !Object.prototype.hasOwnProperty.call(map, section)) return null;
  return map[section];
}

/** Reads `section` from the hash query (`#/play?section=X`) or the search string. */
export function currentSection(): string | null {
  const hash = typeof window === 'undefined' ? '' : window.location.hash;
  const query = hash.includes('?') ? hash.slice(hash.indexOf('?')) : window.location.search;
  return new URLSearchParams(query).get('section');
}

export function useSectionFollow(map: SectionMap): void {
  useEffect(() => {
    const follow = () => {
      const anchor = sectionAnchor(currentSection(), map);
      if (!anchor) return;
      document.getElementById(anchor)?.scrollIntoView({ block: 'start' });
    };
    follow();
    window.addEventListener('hashchange', follow);
    window.addEventListener('popstate', follow);
    return () => {
      window.removeEventListener('hashchange', follow);
      window.removeEventListener('popstate', follow);
    };
  }, [map]);
}
