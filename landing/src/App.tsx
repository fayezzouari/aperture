import { Nav } from "./components/Nav";
import { Footer } from "./components/Footer";
import { Hero } from "./sections/Hero";
import { Ideas } from "./sections/Ideas";
import { Tour } from "./sections/Tour";
import { Workflows } from "./sections/Workflows";
import { Operate } from "./sections/Operate";
import { Run } from "./sections/Run";

export default function App() {
  return (
    <div className="wrap">
      <Nav />
      <main id="top">
        <Hero />
        <Ideas />
        <Tour />
        <Workflows />
        <Operate />
        <Run />
      </main>
      <Footer />
    </div>
  );
}
