import {
  INTENT_CONTRACT_VERSION,
  deriveIntents,
  evaluateIntentActionability,
  groupIntents,
  recordDeliveryResult,
  reconcileDeliveryState,
  safeDeliveryRecord,
  safeIntentPayload,
} from "../src/notification-intent.mjs";

const MAX_BYTES = 512 * 1024; // 512 KB limit

function stableNormalize(value) {
  if (Array.isArray(value)) return value.map(stableNormalize);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, stableNormalize(value[key])]),
    );
  }
  if (typeof value === "number") return Object.is(value, -0) ? 0 : value;
  return value;
}

function exitWithError(message) {
  process.stderr.write(`CLI Error: ${message}\n`);
  process.exit(1);
}

function main() {
  let rawData = "";
  let totalBytes = 0;

  process.stdin.setEncoding("utf8");

  process.stdin.on("data", (chunk) => {
    totalBytes += Buffer.byteLength(chunk, "utf8");
    if (totalBytes > MAX_BYTES) {
      exitWithError(`Input size limit exceeded (max ${MAX_BYTES} bytes)`);
    }
    rawData += chunk;
  });

  process.stdin.on("end", () => {
    if (totalBytes > MAX_BYTES) {
      exitWithError(`Input size limit exceeded (max ${MAX_BYTES} bytes)`);
    }

    let request;
    try {
      request = JSON.parse(rawData);
    } catch {
      exitWithError("Malformed JSON input");
      return;
    }

    if (!request || typeof request !== "object" || Array.isArray(request)) {
      exitWithError("Invalid request object");
      return;
    }

    if (request.contract_version !== INTENT_CONTRACT_VERSION) {
      exitWithError("Contract version mismatch");
      return;
    }

    const action = String(request.action || "").trim();
    let result;

    try {
      if (action === "deriveIntents" || action === "derive_intents") {
        const previousState = request.previous_state ?? request.previousState ?? null;
        const currentState = request.current_state ?? request.currentState ?? null;
        const policy = request.policy ?? {};
        result = deriveIntents(previousState, currentState, policy);
      } else if (action === "groupIntents" || action === "group_intents") {
        const intents = request.intents ?? [];
        result = groupIntents(intents);
      } else if (action === "safeIntentPayload" || action === "safe_intent_payload") {
        const intent = request.intent ?? null;
        result = safeIntentPayload(intent);
      } else if (action === "evaluateIntentActionability" || action === "evaluate_intent_actionability") {
        const intent = request.intent ?? null;
        const currentState = request.current_state ?? request.currentState ?? null;
        result = evaluateIntentActionability(intent, currentState);
      } else if (action === "safeDeliveryRecord" || action === "safe_delivery_record") {
        const record = request.record ?? null;
        result = safeDeliveryRecord(record);
      } else if (action === "reconcileDeliveryState" || action === "reconcile_delivery_state") {
        const existingRecords = request.existing_records ?? request.existingRecords ?? [];
        const derivedIntents = request.derived_intents ?? request.derivedIntents ?? [];
        const currentState = request.current_state ?? request.currentState ?? null;
        const options = request.options ?? {};
        result = reconcileDeliveryState(existingRecords, derivedIntents, currentState, options);
      } else if (action === "recordDeliveryResult" || action === "record_delivery_result") {
        const existingRecords = request.existing_records ?? request.existingRecords ?? [];
        const targetIntentIds = request.target_intent_ids ?? request.targetIntentIds ?? [];
        const deliveryResult = request.delivery_result ?? request.deliveryResult ?? {};
        const options = request.options ?? {};
        result = recordDeliveryResult(existingRecords, targetIntentIds, deliveryResult, options);
      } else {
        exitWithError("Unrecognized action");
        return;
      }
    } catch {
      exitWithError("Semantic execution failed");
      return;
    }

    const response = {
      contract_version: INTENT_CONTRACT_VERSION,
      action: action,
      status: "ok",
      result: result,
    };

    const outputJson = JSON.stringify(stableNormalize(response));
    process.stdout.write(outputJson + "\n");
  });

  process.stdin.on("error", () => {
    exitWithError("Error reading stdin");
  });
}

main();
