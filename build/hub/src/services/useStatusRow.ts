import { useSyncExternalStore } from 'react';
import { statusRowVisible, subscribeStatusSources } from './rowynStatusSources';

/** True while either Home status source exists on this host. */
export function useStatusRow(): boolean {
  return useSyncExternalStore(subscribeStatusSources, statusRowVisible, () => false);
}
