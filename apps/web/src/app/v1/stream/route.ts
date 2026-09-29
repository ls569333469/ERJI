export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  const response = await fetch((process.env.ERJI_API_ORIGIN || "http://127.0.0.1:18771") + "/v1/stream", {
    headers: { "Last-Event-ID": request.headers.get("last-event-id") || "0", "Accept": "text/event-stream", "Cookie": request.headers.get("cookie") || "" },
    cache: "no-store", signal: request.signal,
  });
  return new Response(response.body, { status: response.status, headers: {
    "Content-Type": "text/event-stream", "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
  } });
}
