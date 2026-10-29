import { http, HttpResponse, type JsonBodyType } from "msw";
import { setupServer } from "msw/node";

export const API = "http://lab.test/api";

export const server = setupServer();

/** Answer GET `path` with `body` (and optionally a status). */
export function get(path: string, body: JsonBodyType, status = 200) {
  server.use(http.get(`${API}${path}`, () => HttpResponse.json(body, { status })));
}

/** Answer every listed GET with an empty list, for pages that make several requests. */
export function emptyLists(...paths: string[]) {
  for (const path of paths) get(path, []);
}
