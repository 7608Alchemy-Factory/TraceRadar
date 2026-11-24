import { createContext, useContext, useEffect, useState } from "react";
import { api, json } from "./api";
import { locale, t, type Locale } from "./i18n";

const key = (language: Locale, value: string) => JSON.stringify([language, value]);
const cached = new Map<string, string>();
const queued = new Set<string>();
const han = /[\u3400-\u9fff]/;
const latin = /[A-Za-z]{3}/;

export const TranslationContext = createContext<(value: string) => string>((value) => value);
export const useTranslation = () => useContext(TranslationContext);

function shouldTranslate(value: string, language: Locale) {
  return language === "en" ? han.test(value) : han.test(value) || latin.test(value);
}

async function requestTranslation(items: string[], language: Locale, signal?: AbortSignal) {
  const response = await api<{ translations: string[] }>("/translation", { ...json("POST", { language, items }), signal });
  items.forEach((item, index) => cached.set(key(language, item), response.translations[index]));
  return response.translations;
}

export function useWorkspaceTranslation(language: Locale, items: string[], enabled: boolean) {
  const [, refresh] = useState(0);
  const [failed, setFailed] = useState(false);
  const requested = JSON.stringify([...new Set(items)].filter((item) => item && shouldTranslate(item, language)));
  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    setFailed(false);
    const items: string[] = JSON.parse(requested);
    const need = items.filter((item) => !cached.has(key(language, item)) && !queued.has(key(language, item)));
    async function run() {
      while (need.length && !disposed) {
        const batch: string[] = [];
        let length = 0;
        while (need.length && batch.length < 16 && length + need[0].length <= 16000) {
          const item = need.shift()!;
          if (cached.has(key(language, item)) || queued.has(key(language, item))) continue;
          batch.push(item);
          length += item.length;
        }
        if (!batch.length) {
          if (need[0]?.length > 16000) need.shift();
          continue;
        }
        batch.forEach((item) => queued.add(key(language, item)));
        try {
          await requestTranslation(batch, language);
          refresh((value) => value + 1);
        } catch {
          if (!disposed) setFailed(true);
          return;
        } finally {
          batch.forEach((item) => queued.delete(key(language, item)));
        }
      }
    }
    void run();
    return () => { disposed = true; };
  }, [enabled, language, requested]);
  return {
    lookup: (value: string) => enabled ? cached.get(key(language, value)) ?? value : value,
    failed: enabled && failed,
  };
}

export function ReferenceTranslation({ title, body }: { title: string; body: string }) {
  const language = locale();
  const [result, setResult] = useState<string[]>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  async function open() {
    setLoading(true);
    setError(false);
    try {
      const parts = body.match(/[\s\S]{1,4000}(?:\s|$)|[\s\S]{1,4000}/gu) ?? [];
      const translated = [await requestTranslation([title], language)];
      for (const part of parts) translated.push(await requestTranslation([part], language));
      setResult(translated.flat());
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }
  return (
    <section className="reference-translation">
      <button type="button" disabled={loading} aria-expanded={!!result}
        onClick={() => result ? setResult(undefined) : void open()}>
        {t(loading ? "正在翻译…" : result ? "收起参考译文" : "生成参考译文")}
      </button>
      {error && <p role="status">{t("翻译暂不可用，请检查模型配置后重试。")}</p>}
      {result && <div lang={language}><small>{t("机器参考译文")}</small><h3>{result[0]}</h3><p className="article-body">{result.slice(1).join("\n")}</p></div>}
    </section>
  );
}
