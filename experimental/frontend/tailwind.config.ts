import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    container: {
      center: true,
      padding: "1rem",
      screens: { "2xl": "1280px" },
    },
    extend: {
      colors: {
        background: "hsl(220 25% 6%)",
        foreground: "hsl(210 40% 98%)",
        muted: "hsl(220 15% 16%)",
        "muted-foreground": "hsl(220 10% 65%)",
        border: "hsl(220 15% 18%)",
        accent: "hsl(190 95% 55%)",
        "accent-foreground": "hsl(220 25% 6%)",
        success: "hsl(150 80% 50%)",
        warning: "hsl(40 95% 55%)",
        danger: "hsl(0 80% 60%)",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;