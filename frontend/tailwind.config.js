/** @type {import('tailwindcss').Config} */
export default {
  // Tell Tailwind which files contain class names to scan.
  // This is how Tailwind's JIT purges unused classes in production.
  content: [
    "./index.html",
    "./src/**/*.{js,jsx,ts,tsx}",
  ],
  theme: {
    extend: {
      // Custom animation for the pulsing active-step highlight
      keyframes: {
        "pulse-soft": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.6" },
        },
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(12px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "progress-fill": {
          "0%": { width: "0%" },
          "100%": { width: "var(--progress-width)" },
        },
      },
      animation: {
        "pulse-soft": "pulse-soft 2s ease-in-out infinite",
        "fade-in": "fade-in 0.5s ease-out forwards",
      },
      fontFamily: {
        // Inter for body text — clean and highly legible
        sans: ["Inter", "system-ui", "sans-serif"],
        // Bricolage Grotesque for display headings — distinctive and modern
        display: ["'Bricolage Grotesque'", "Inter", "sans-serif"],
      },
      colors: {
        // Core brand palette — indigo-to-violet
        brand: {
          50:  "#eef2ff",
          100: "#e0e7ff",
          200: "#c7d2fe",
          400: "#818cf8",
          500: "#6366f1",
          600: "#4f46e5",
          700: "#4338ca",
          900: "#312e81",
        },
      },
    },
  },
  plugins: [
    // Enables line-clamp-{n} utilities used in SummaryCard and TopQuotes
    require("@tailwindcss/line-clamp"),
  ],
};
