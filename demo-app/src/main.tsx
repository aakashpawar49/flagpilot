import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import flagsmith from '@flagsmith/flagsmith'
import { FlagsmithProvider } from '@flagsmith/flagsmith/react'
import './styles.css'
import App from './App.tsx'

const visitorId = localStorage.getItem('visitor_id') || crypto.randomUUID();
localStorage.setItem('visitor_id', visitorId);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <FlagsmithProvider
      flagsmith={flagsmith}
      options={{
        environmentID: import.meta.env.VITE_FLAGSMITH_ENVIRONMENT_ID,
        identity: visitorId,
        enableEvents: true,
      }}
    >
      <App />
    </FlagsmithProvider>
  </StrictMode>,
)