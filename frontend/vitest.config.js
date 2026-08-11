import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vitest/config';
export default defineConfig({
    plugins: [vue()],
    test: {
        environment: 'jsdom',
        globals: true,
        include: ['tests/**/*.{spec,test}.ts'],
        coverage: {
            reporter: ['text', 'html'],
        },
    },
});
