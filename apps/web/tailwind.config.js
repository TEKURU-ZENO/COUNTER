/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#111110", soft: "#3A3A37", muted: "#5E5E59" },
        board: { DEFAULT: "#FFFFFF", sunk: "#F3F3F1", line: "#E4E4E0" },
        steel: { 950: "#121416", 900: "#1A1D20", 800: "#25292D", 700: "#363B40", 400: "#9AA1A8", 200: "#D5D9DD" },
        carbon: { DEFAULT: "#FFF3B0", deep: "#F5E07A", ink: "#2B2610" },
        paid: { DEFAULT: "#0F7A44", soft: "#E3F4EA" },
        pending: { DEFAULT: "#9A5B00", soft: "#FCF0DC" },
        declined: { DEFAULT: "#B3261E", soft: "#FBE7E5" },
        paypal: { yellow: "#FFC439", blue: "#003087" },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      borderRadius: { pin: "20px" },
      boxShadow: {
        pin: "0 1px 2px rgba(17,17,16,0.06), 0 8px 24px -12px rgba(17,17,16,0.18)",
        lift: "0 2px 4px rgba(17,17,16,0.08), 0 16px 32px -14px rgba(17,17,16,0.28)",
        ticket: "0 2px 3px rgba(43,38,16,0.12), 0 18px 30px -18px rgba(43,38,16,0.45)",
      },
      transitionTimingFunction: { out: "cubic-bezier(0.16, 1, 0.3, 1)" },
    },
  },
  plugins: [],
};
