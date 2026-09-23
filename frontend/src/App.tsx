import NormalApp from './NormalApp';
import SubmissionPreviewApp from './SubmissionPreviewApp';

const PREVIEW = import.meta.env.VITE_DRISHTI_DEMO_MODE === 'true' && import.meta.env.VITE_DRISHTI_SUBMISSION_PREVIEW === 'true';

export default function App() {
  return PREVIEW ? <SubmissionPreviewApp /> : <NormalApp />;
}
