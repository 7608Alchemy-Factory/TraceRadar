import { locales, t, type Locale, type Theme } from "./i18n";
import { Icon } from "./ui";

export function Preferences({ language, theme, onLanguage, onTheme }: {
  language: Locale; theme: Theme;
  onLanguage: (value: Locale) => void; onTheme: (value: Theme) => void;
}) {
  return (
    <div className="preferences">
      <select aria-label={t("界面语言")} value={language} onChange={(event) => onLanguage(event.target.value as Locale)}>
        {locales.map(({ id, label }) => <option key={id} value={id} lang={id}>{label}</option>)}
      </select>
      <div className="theme-switch">
        {(["light", "dark"] as const).map((mode) => (
          <button key={mode} type="button" aria-label={t(mode === "light" ? "亮色模式" : "暗色模式")}
            title={t(mode === "light" ? "亮色模式" : "暗色模式")} aria-pressed={theme === mode}
            onClick={() => onTheme(mode)}><Icon name={mode === "light" ? "sun" : "moon"} size={16} /></button>
        ))}
      </div>
    </div>
  );
}
