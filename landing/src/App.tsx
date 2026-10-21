import { Footer } from "./components/Footer";
import { Nav } from "./components/Nav";
import { Hero } from "./sections/Hero";
import { Ideas } from "./sections/Ideas";
import { Operate } from "./sections/Operate";
import { Run } from "./sections/Run";
import { Overrides } from "./sections/Overrides";
import { Workflows } from "./sections/Workflows";

export default function App() {
  return (
    <>
      <Nav />
      <main id="top">
        <Hero />
        <Ideas />
        <Overrides />
        <Workflows />
        <Operate />
        <Run />
      </main>
      <Footer />
    </>
  );
}
