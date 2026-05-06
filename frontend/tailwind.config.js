/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      colors: {
        brand: {
          1: '#4f46e5',
          2: '#7c3aed',
          3: '#06b6d4',
        },
      },
      animation: {
        pulseDot: 'pulseDot 1.6s infinite',
      },
      keyframes: {
        pulseDot: {
          '0%':   { boxShadow: '0 0 0 0 rgba(34,211,238,0.6)' },
          '70%':  { boxShadow: '0 0 0 12px rgba(34,211,238,0)' },
          '100%': { boxShadow: '0 0 0 0 rgba(34,211,238,0)' },
        },
      },
    },
  },
  plugins: [],
};
