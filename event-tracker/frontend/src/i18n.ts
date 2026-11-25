import { messages } from "./messages";

export type Locale = "zh-Hans" | "zh-Hant" | "en";
export type Theme = "light" | "dark";

export const locales: ReadonlyArray<{ id: Locale; label: string }> = [
  { id: "zh-Hans", label: "简体中文" },
  { id: "zh-Hant", label: "繁體中文" },
  { id: "en", label: "English" },
];

const savedLocale = localStorage.getItem("traceradar-locale");
let language = locales.find(({ id }) => id === savedLocale)?.id ?? "zh-Hans";
document.documentElement.lang = language;

export function locale() {
  return language;
}

export function setLocale(value: Locale) {
  language = value;
  document.documentElement.lang = value;
  localStorage.setItem("traceradar-locale", value);
}

export function t(key: string, ...values: Array<string | number>) {
  const entry = Object.hasOwn(messages, key) ? messages[key] : undefined;
  const message =
    (language === "en"
      ? entry?.[0]
      : language === "zh-Hant"
        ? entry?.[1]
        : key) ?? key;
  return message.replace(/\{(\d+)\}/g, (match, index) =>
    String(values[Number(index)] ?? match),
  );
}

export function applyTheme(value: Theme) {
  document.documentElement.dataset.theme = value;
  localStorage.setItem("traceradar-theme", value);
}

export function initialTheme(): Theme {
  const saved = localStorage.getItem("traceradar-theme");
  if (saved === "light" || saved === "dark") return saved;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
