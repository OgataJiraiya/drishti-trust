import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { env } from 'node:process';
const preview = env.VITE_DRISHTI_DEMO_MODE === 'true' && env.VITE_DRISHTI_SUBMISSION_PREVIEW === 'true';
export default defineConfig({plugins:[react()],build:preview?{modulePreload:{polyfill:false}}:undefined,server:{proxy:{'/api':'http://127.0.0.1:8000','/health':'http://127.0.0.1:8000'}},test:{environment:'jsdom',setupFiles:'./src/test/setup.ts',css:true}});
