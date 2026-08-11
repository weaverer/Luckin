export interface ErrorDetail {
  field: string | null;
  code: string;
  message: string;
}

export interface ErrorEnvelope {
  code: number;
  message: string;
  data: null;
  errors: ErrorDetail[];
  request_id: string;
  timestamp: string;
}

export class WorkbenchApiError extends Error {
  constructor(
    readonly httpStatus: number,
    readonly envelope: ErrorEnvelope,
  ) {
    super(envelope.message);
    this.name = "WorkbenchApiError";
  }

  get code(): number {
    return this.envelope.code;
  }

  get details(): ErrorDetail[] {
    return this.envelope.errors;
  }
}

export function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.code === "number" &&
    candidate.code !== 0 &&
    typeof candidate.message === "string" &&
    candidate.data === null &&
    Array.isArray(candidate.errors) &&
    typeof candidate.request_id === "string" &&
    typeof candidate.timestamp === "string"
  );
}
