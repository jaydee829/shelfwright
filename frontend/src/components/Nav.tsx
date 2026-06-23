import { NavLink } from 'react-router'
import LineIcon, { type LineIconName } from './LineIcon'
import './Nav.css'

const ITEMS: { to: string; label: string; icon: LineIconName; end: boolean }[] = [
  { to: '/', label: 'Chat', icon: 'chat', end: true },
  { to: '/history', label: 'History', icon: 'history', end: false },
  { to: '/recommendations', label: 'Picks', icon: 'picks', end: false },
  { to: '/analysis', label: 'Analysis', icon: 'analysis', end: false },
  { to: '/add', label: 'Add', icon: 'add', end: false },
]

export default function Nav() {
  return (
    <nav className="nav" aria-label="Primary">
      {ITEMS.map((item) => (
        <NavLink key={item.to} to={item.to} end={item.end} className="nav-item">
          <span className="nav-icon">
            <LineIcon name={item.icon} />
          </span>
          <span className="nav-label">{item.label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
