import { BrowserRouter, Route, Routes } from 'react-router'
import { AuthProvider, useAuth } from './auth/AuthContext'
import AppShell from './components/AppShell'
import NotInvited from './components/NotInvited'
import SignIn from './components/SignIn'
import AddBookView from './views/AddBookView'
import AnalysisView from './views/AnalysisView'
import ChatView from './views/ChatView'
import HistoryEditView from './views/HistoryEditView'
import HistoryView from './views/HistoryView'
import RecommendationsView from './views/RecommendationsView'

function Gate() {
  const { status } = useAuth()
  if (status === 'loading') return <div style={{ display: 'grid', placeItems: 'center', minHeight: '100vh' }}>Loading…</div>
  if (status === 'signedOut') return <SignIn />
  if (status === 'notInvited') return <NotInvited />
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<ChatView />} />
          <Route path="history" element={<HistoryView />} />
          <Route path="history/:id/edit" element={<HistoryEditView />} />
          <Route path="recommendations" element={<RecommendationsView />} />
          <Route path="analysis" element={<AnalysisView />} />
          <Route path="add" element={<AddBookView />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  )
}
