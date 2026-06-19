import type { GetServerSideProps } from 'next'

export const getServerSideProps: GetServerSideProps = async () => ({
  redirect: {
    destination: '/workspace?tab=garden',
    permanent: false,
  },
})

export default function GardenRedirectPage() {
  return null
}
