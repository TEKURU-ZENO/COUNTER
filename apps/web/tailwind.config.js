/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        terracotta: {
          50: "#fff5f2",
          100: "#ffe8e1",
          500: "#e05833",
          600: "#cc4422",
          700: "#a93318",
          800: "#862915",
          900: "#692213",
        },
        crust: {
          50: "#fdfbf7",
          100: "#f9f3e8",
          200: "#f3e4cb",
          500: "#d99b38",
          600: "#be7c26",
          700: "#9b5e20",
        },
        olive: {
          50: "#f5f7f3",
          100: "#e7ebe2",
          500: "#5c7c4b",
          600: "#47623a",
          700: "#374c2d",
        },
        cream: {
          50: "#faf8f5",
          100: "#f4f0e8",
          200: "#eae3d4",
          300: "#ded2bd",
        },
        charcoal: {
          800: "#1e2124",
          900: "#141618",
          950: "#0b0c0e",
        },
      },
      fontFamily: {
        serif: ["Georgia", "Cambria", "serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
