import type { components } from "./api-schema";

type Schemas = components["schemas"];
export type DocumentView = Schemas["DocumentView"];
export type DocumentList = Schemas["DocumentList"];
export type Citation = Schemas["Citation"];
export type Answer = Schemas["Answer"];
export type WorkspaceConfig = Schemas["WorkspaceConfig"];
export type Question = Schemas["Question"];

export class ApiError extends Error {
  constructor(readonly status: number, message: string) { super(message); }
}

async function request(path: string, token?: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (typeof init.body === "string") headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/v1${path}`, { ...init, headers, cache: "no-store" });
  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as Schemas["ErrorResponse"] | null;
    throw new ApiError(response.status, error?.error?.message ?? "The service is unavailable. Try again.");
  }
  return response;
}

export async function api<T>(path: string, token?: string, init?: RequestInit): Promise<T> {
  const response = await request(path, token, init);
  return response.status === 204 ? undefined as T : await response.json() as T;
}

export async function fileBlob(path: string, token: string, signal?: AbortSignal) {
  return (await request(path, token, { signal })).blob();
}
