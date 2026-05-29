/** @type {import('tailwindcss').Config} */
module.exports = {
  // Scan the compiled deploy HTML (which now contains the transpiled JS string
  // with all className literals) plus the JSX source as a belt-and-suspenders.
  content: [
    './_site/index.html',
    './soccer_team_app.jsx',
    './soccer_team_app_standalone_backup.html',
  ],
  // Classes we build dynamically (string concatenation, ternaries against
  // jersey colors, etc.) that the scanner might miss.
  safelist: [
    'stripes-bg',
    'font-display',
    'font-sans-pro',
    { pattern: /^(bg|text|border|ring)-(lime|stone|red|amber|emerald|sky|blue|violet|fuchsia|rose|orange|yellow|green|teal|cyan|indigo|purple|pink|white|black)-(50|100|200|300|400|500|600|700|800|900|950)$/ },
    { pattern: /^(bg|text)-(white|black)\/(5|10|15|20|25|30|40|50|60|70|80|90)$/ },
  ],
  theme: {
    extend: {
      fontFamily: {
        display: ['Outfit', 'system-ui', 'sans-serif'],
        'sans-pro': ['Outfit', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
};
