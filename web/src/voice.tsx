import {createRoot} from 'react-dom/client'
import {VoiceCapture} from './components/desktop/VoiceCapture'
import './styles.css'

const root = document.getElementById('root')
if (!root) throw Error('Missing voice capture root')
createRoot(root).render(<VoiceCapture />)
