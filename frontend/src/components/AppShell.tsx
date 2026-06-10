import { Outlet } from 'react-router'
import './AppShell.css'
import Nav from './Nav'
import TopBar from './TopBar'

export default function AppShell() {
  return (
    <>
      <TopBar />
      <Nav />
      <main className="content">
        <Outlet />
      </main>
    </>
  )
}
