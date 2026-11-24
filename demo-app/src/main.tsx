import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import App from './App.tsx'
import flagsmith from '@flagsmith/flagsmith'
import { FlagsmithProvider } from '@flagsmith/flagsmith/react'

let visitorId = localStorage.getItem('lumen_visitor_id')
if (!visitorId) {
  visitorId = 'visitor_' + Math.random().toString(36).substring(2, 15)
  localStorage.setItem('lumen_visitor_id', visitorId)
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <FlagsmithProvider
      flagsmith={flagsmith}
      options={{
        environmentID: import.meta.env.VITE_FLAGSMITH_ENVIRONMENT_ID || 'dummy-env-id',
        identity: visitorId,
        enableEvents: true,
      }}
    >
      <App />
    </FlagsmithProvider>
  </StrictMode>,
)
