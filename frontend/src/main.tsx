import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { applyTheme, resolveInitialTheme } from './theme'
import '@fontsource-variable/inter'
import '@fontsource-variable/literata'
import './index.css'

applyTheme(resolveInitialTheme())

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
