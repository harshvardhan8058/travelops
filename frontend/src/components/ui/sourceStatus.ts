import type { SystemMode } from '@/api/types';

export type EffectiveStatusValue = 'LIVE' | 'FIXTURE' | 'OFF' | 'SIMULATED' | '…';

export interface EffectiveStatus {
  label: 'LLM' | 'FLT' | 'WX' | 'NOTIFY';
  value: EffectiveStatusValue;
  description: string;
  tone: 'ok' | 'info' | 'warn' | 'neutral';
}

const LOADING_DESCRIPTION = 'Effective system mode has not been recorded in this view yet.';

/**
 * Presents only effective fields recorded by GET /system/mode.
 *
 * In particular, a configured SMTP transport is not a live-delivery claim. The backend records
 * `real_email_enabled` separately and that flag must be true before this surface says LIVE.
 */
export function effectiveStatuses(mode?: SystemMode): EffectiveStatus[] {
  if (!mode) {
    return (['LLM', 'FLT', 'WX', 'NOTIFY'] as const).map((label) => ({
      label,
      value: '…',
      description: LOADING_DESCRIPTION,
      tone: 'neutral',
    }));
  }

  const notificationIsLive =
    mode.real_email_enabled &&
    (mode.notification_mode === 'gmail' || mode.notification_mode === 'mailtrap');

  return [
    {
      label: 'LLM',
      value: mode.llm_mode.toUpperCase() as 'LIVE' | 'FIXTURE' | 'OFF',
      description:
        mode.llm_mode === 'live'
          ? 'Recorded effective LLM mode is live. This describes model access, not authority to execute.'
          : mode.llm_mode === 'fixture'
            ? 'Recorded effective LLM mode replays committed fixture output.'
            : 'Recorded effective LLM mode is off; recovery uses the deterministic fallback path.',
      tone: mode.llm_mode === 'live' ? 'ok' : mode.llm_mode === 'fixture' ? 'info' : 'warn',
    },
    {
      label: 'FLT',
      value: mode.flight_status_mode.toUpperCase() as 'LIVE' | 'FIXTURE',
      description:
        mode.flight_status_mode === 'live'
          ? 'Recorded effective flight-status mode reads the live provider.'
          : 'Recorded effective flight-status mode reads committed fixture data.',
      tone: mode.flight_status_mode === 'live' ? 'ok' : 'info',
    },
    {
      label: 'WX',
      value: mode.weather_mode.toUpperCase() as 'LIVE' | 'FIXTURE',
      description:
        mode.weather_mode === 'live'
          ? 'Recorded effective weather mode reads the live provider.'
          : 'Recorded effective weather mode reads committed fixture observations.',
      tone: mode.weather_mode === 'live' ? 'ok' : 'info',
    },
    {
      label: 'NOTIFY',
      value: notificationIsLive ? 'LIVE' : 'SIMULATED',
      description: notificationIsLive
        ? `Real email is enabled through the recorded ${mode.notification_mode} transport. This is a capability status, not a claim that a message was delivered.`
        : `Recorded notification transport is ${mode.notification_mode}, but real_email_enabled is false or the transport is non-email. Notifications are simulated; no live delivery is claimed.`,
      tone: notificationIsLive ? 'ok' : 'info',
    },
  ];
}
