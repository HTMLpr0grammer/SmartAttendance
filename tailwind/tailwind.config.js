// Tailwind CSS build configuration (same theme the pages used with the Tailwind Play CDN).
// Rebuild static/css/app.css after adding new classes to templates or JavaScript – see README.md.
module.exports = {
  content: {
    relative: true,
    files: [
      "../templates/**/*.html",
      "../static/js/**/*.js",
      "!../static/js/vendor/**",
      "../attendance/**/*.py",
    ],
  },
  theme: {
    extend: {
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"] },
      colors: {
        brand: { 50: "#effaf3", 100: "#d8f3e1", 200: "#b3e6c6", 300: "#80d2a3", 400: "#4bb67c", 500: "#289a60",
                 600: "#1a7c4c", 700: "#16633f", 800: "#144f34", 900: "#11412c", 950: "#08251a" },
        gold: { 400: "#e8b931", 500: "#d4a017", 600: "#b08510" },
      },
    },
  },
  plugins: [require("@tailwindcss/forms")],
};
