// sat69.watr.mx — sirve la landing estática, pero proxya el MCP a Render para
// que el servidor viva bajo el dominio de marca (no el onrender.com crudo).
//
// Se proxya SÓLO lo público del protocolo:
//   • /connect            → endpoint MCP (streamable-HTTP; /mcp da 404 en origin)
//   • /.well-known/oauth* → discovery de OAuth (AuthKit), si está encendido
// Los endpoints admin (/refresh, /reload, /health) NO se exponen aquí: quedan
// sólo en onrender, tras el bearer.
//
// ponytail: proxy por prefijo; la Response se devuelve tal cual (SSE pasa directo).
// El Host lo fija fetch() desde la URL destino, así Render enruta bien.
const ORIGIN = "https://sat69-mcp.onrender.com";

export default {
  async fetch(request, env) {
    const { pathname, search } = new URL(request.url);
    const proxied =
      pathname === "/connect" ||
      pathname.startsWith("/connect/") ||
      pathname.startsWith("/.well-known/oauth");
    if (proxied) {
      return fetch(new Request(ORIGIN + pathname + search, request));
    }
    return env.ASSETS.fetch(request);
  },
};
