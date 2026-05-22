/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./pages/**/*.{js,jsx}', './components/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        surface:  '#0f1117',
        card:     '#1a1d27',
        border:   '#2a2d3a',
        accent:   '#6c63ff',
        up:       '#22c55e',
        down:     '#ef4444',
        warn:     '#f59e0b',
        muted:    '#6b7280',
      },
      fontFamily: { mono: ['JetBrains Mono', 'Fira Code', 'monospace'] },
    },
  },
  plugins: [],
}
