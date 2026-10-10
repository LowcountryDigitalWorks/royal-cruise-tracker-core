const GITHUB_REF = 'main';
const TARGET_PROOF_DOMAIN = 'royal-cruise-scheduler-v1\0';

function dispatchError(code) {
  const error = new Error(code);
  error.name = 'SchedulerWakeError';
  return error;
}

function requiredDispatchToken(env) {
  const token = String(env?.GITHUB_ACTIONS_DISPATCH_TOKEN || '').trim();
  if (!token) throw dispatchError('scheduler_wake_secret_missing');
  return token;
}

function schedulerProofKey(env) {
  return String(env?.SCHEDULER_TARGET_PROOF_KEY || '').trim();
}

export function scheduledTargetIso(controller) {
  const value = Number(controller?.scheduledTime);
  if (!Number.isFinite(value) || value <= 0) return '';
  try {
    return new Date(value).toISOString();
  } catch {
    return '';
  }
}

function hex(bytes) {
  return Array.from(new Uint8Array(bytes), (value) => value.toString(16).padStart(2, '0')).join('');
}

export async function schedulerTargetProof(target, proofKey) {
  const normalizedTarget = String(target || '').trim();
  const secret = String(proofKey || '').trim();
  if (!normalizedTarget || !secret) return '';
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'],
  );
  const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(`${TARGET_PROOF_DOMAIN}${normalizedTarget}`));
  return hex(signature);
}

export async function dispatchScheduledRoyalWake(controller, env, fetcher = fetch) {
  const cron = String(controller?.cron || '').trim();
  const expectedCron = String(env?.ROYAL_WAKE_CRON || '').trim();
  const dispatchUrl = String(env?.GITHUB_ACTIONS_DISPATCH_URL || '').trim();
  if (!expectedCron) throw dispatchError('scheduler_wake_cron_missing');
  if (cron !== expectedCron) throw dispatchError('scheduler_wake_unexpected_cron');
  if (!dispatchUrl.startsWith('https://api.github.com/repos/') || !dispatchUrl.endsWith('/dispatches')) throw dispatchError('scheduler_wake_dispatch_url_invalid');

  const token = requiredDispatchToken(env);
  const proofKey = schedulerProofKey(env);
  const target = scheduledTargetIso(controller);
  const inputs = { scheduler_wake: 'true' };
  // scheduledTime is Cloudflare's authoritative UTC time for this Cron event. Sign
  // that value only with the dedicated proof key so the GitHub workflow can tell a
  // Worker-originated target from a human/manual workflow_dispatch input without
  // coupling proof integrity to the GitHub bearer token. If the proof key is absent,
  // dispatch still proceeds and GitHub safely falls back to due-gate telemetry.
  if (target) {
    inputs.scheduler_target_at = target;
    inputs.scheduler_target_proof = await schedulerTargetProof(target, proofKey);
  }

  const response = await fetcher(dispatchUrl, {
    method: 'POST',
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      'User-Agent': 'ldw-royal-cruise-scheduler',
      'X-GitHub-Api-Version': '2026-03-10',
    },
    body: JSON.stringify({
      ref: GITHUB_REF,
      inputs,
    }),
  });

  // GitHub's current workflow-dispatch endpoint returns 200 with run metadata.
  // Accept 204 as well for compatibility with older API behavior. Never read or
  // log the response body because it is unnecessary for the wake contract.
  if (response.status !== 200 && response.status !== 204) {
    throw dispatchError(`scheduler_wake_dispatch_http_${response.status}`);
  }

  return { ok: true, status: response.status };
}
