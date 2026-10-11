import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { JevBattleView } from './components/JevBattleView.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {new URLSearchParams(window.location.search).get('battle') === '1' ? <JevBattleView /> : <App />}
  </StrictMode>,
)
