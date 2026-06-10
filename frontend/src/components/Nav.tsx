import { NavLink } from 'react-router'
import './Nav.css'

const ITEMS = [
  { to: '/', label: 'Chat', icon: '💬', end: true },
  { to: '/history', label: 'History', icon: '📚', end: false },
  { to: '/recommendations', label: 'Picks', icon: '✨', end: false },
  { to: '/analysis', label: 'Analysis', icon: '📊', end: false },
  { to: '/add', label: 'Add', icon: '➕', end: false },
]

export default function Nav() {
  return (
    <nav className="nav" aria-label="Primary">
      {ITEMS.map((item) => (
        <NavLink key={item.to} to={item.to} end={item.end} className="nav-item">
          <span className="nav-icon" aria-hidden>{item.icon}</span>
          <span className="nav-label">{item.label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
