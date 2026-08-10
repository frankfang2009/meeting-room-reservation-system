import { validateAuthenticatedContext } from "./domain.js";

export async function readAuthenticatedContext(client) {
  const session = await client.getSession();
  const bootstrap = await client.getBootstrap();
  return validateAuthenticatedContext(session, bootstrap);
}

export async function reauthenticateContext(client, { username, password }) {
  // Confirm that the service is reachable before sending credentials. The
  // second session read is authoritative and must follow the login response.
  await client.getSession();
  await client.login(String(username || "").trim(), password);
  return readAuthenticatedContext(client);
}

export function scopedAppKey(session, scopeVersion) {
  const id = session?.currentUser?.id || "unknown";
  const role = session?.currentUser?.role || "unknown";
  return `${id}:${role}:${Number(scopeVersion) || 0}`;
}
