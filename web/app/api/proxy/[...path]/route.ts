/**
 * Прокси к backend.
 *
 * Basic-авторизация добавляется здесь, на сервере: логин и пароль не должны
 * попадать в браузер и в NEXT_PUBLIC_* переменные.
 */
import { NextRequest, NextResponse } from "next/server";

const API_URL = (process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000")
  .replace(/\/$/, "");

function authHeader(): Record<string, string> {
  const user = process.env.ADMIN_USERNAME;
  const pass = process.env.ADMIN_PASSWORD;
  if (!user || !pass) return {};
  const token = Buffer.from(`${user}:${pass}`).toString("base64");
  return { Authorization: `Basic ${token}` };
}

async function forward(req: NextRequest, path: string[]) {
  const search = req.nextUrl.search || "";
  const target = `${API_URL}/api/${path.join("/")}${search}`;

  const init: RequestInit = {
    method: req.method,
    headers: {
      "Content-Type": "application/json",
      ...authHeader(),
    },
    cache: "no-store",
  };

  if (!["GET", "HEAD"].includes(req.method)) {
    init.body = await req.text();
  }

  try {
    const res = await fetch(target, init);
    const text = await res.text();
    return new NextResponse(text, {
      status: res.status,
      headers: { "Content-Type": res.headers.get("content-type") || "application/json" },
    });
  } catch (err) {
    return NextResponse.json(
      { detail: `Backend недоступен: ${(err as Error).message}` },
      { status: 502 },
    );
  }
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function POST(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function PATCH(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function PUT(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function DELETE(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}

export const dynamic = "force-dynamic";
