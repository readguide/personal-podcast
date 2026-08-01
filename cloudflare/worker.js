const REPOSITORY = "readguide/personal-podcast";
const AUDIO_PATH = /^\/audio\/([A-Za-z0-9][A-Za-z0-9._-]*\.(?:aac|m4a|mp3))$/;

const MIME_TYPES = {
  aac: "audio/aac",
  m4a: "audio/mp4",
  mp3: "audio/mpeg",
};

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return Response.json({ status: "ok", storage: "github-releases" });
    }

    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", {
        status: 405,
        headers: { Allow: "GET, HEAD" },
      });
    }

    const match = url.pathname.match(AUDIO_PATH);
    if (!match) {
      return new Response("Not found", { status: 404 });
    }

    const filename = match[1];
    const extension = filename.slice(filename.lastIndexOf(".") + 1).toLowerCase();
    const episodeId = filename.slice(0, -(extension.length + 1));
    const tag = `episode-${episodeId}`;
    const upstreamUrl =
      `https://github.com/${REPOSITORY}/releases/download/` +
      `${encodeURIComponent(tag)}/${encodeURIComponent(filename)}`;

    const upstreamHeaders = new Headers();
    for (const name of ["Range", "If-Modified-Since", "If-None-Match"]) {
      const value = request.headers.get(name);
      if (value) upstreamHeaders.set(name, value);
    }

    const upstream = await fetch(upstreamUrl, {
      method: request.method,
      headers: upstreamHeaders,
      redirect: "follow",
    });
    const headers = new Headers(upstream.headers);
    headers.set("Content-Type", MIME_TYPES[extension]);
    headers.set("Accept-Ranges", "bytes");
    headers.set("Access-Control-Allow-Origin", "*");
    headers.set("Cache-Control", "public, max-age=3600");
    headers.delete("Content-Disposition");
    headers.delete("Set-Cookie");

    return new Response(request.method === "HEAD" ? null : upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers,
    });
  },
};
